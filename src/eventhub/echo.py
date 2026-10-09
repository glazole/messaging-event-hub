"""Независимый Echo Service: никаких Telegram SDK, токенов и административных ключей."""
import hmac
import logging
import os

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("eventhub.echo")
app = FastAPI(title="Echo Business Service")


class Envelope(BaseModel):
    event_id: str
    event_type: str
    workspace_id: str
    connection_id: str
    payload: dict = Field(default_factory=dict)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/events")
async def receive(event: Envelope, x_hub_key: str | None = Header(default=None)):
    secret = os.environ.get("CALLBACK_SHARED_KEY", "")
    if not secret or not x_hub_key or not hmac.compare_digest(secret, x_hub_key):
        raise HTTPException(403, "Forbidden")

    # Delivery events with no text are acknowledged without sending an answer.
    if event.event_type != "message.received":
        return {"ack": True, "skipped": "unsupported event type"}
    incoming_text = event.payload.get("text")
    if not isinstance(incoming_text, str) or not incoming_text.strip():
        return {"ack": True, "skipped": "no text"}
    # No replies to arbitrary system events, only routed text messages.
    reply = "Эхо: " + incoming_text[:3900]
    request = {
        "event_id": event.event_id,
        "service_key": "echo-service",
        "text": reply,
    }
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            result = await client.post(
                os.environ.get("EVENT_HUB_API_URL", "http://api:8000") + "/v1/service-replies",
                json=request,
                headers={"X-Hub-Key": secret},
            )
            result.raise_for_status()
            data = result.json()
    except httpx.HTTPError as exc:
        log.warning("Echo reply queue failed for event=%s: %s", event.event_id, type(exc).__name__)
        raise HTTPException(503, "Unable to queue echo reply") from None

    log.info("Echo reply queued event=%s command=%s created=%s", event.event_id, data.get("command_id"), data.get("created"))
    return {"ack": True, "command_id": data.get("command_id"), "created": data.get("created")}
