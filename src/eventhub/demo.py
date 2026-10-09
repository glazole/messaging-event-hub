"""Минимальный потребитель событий, тестирует маршрутизацию."""
import logging
import os
import hmac
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
log = logging.getLogger("eventhub.demo")
app = FastAPI(title="Demo Consumer")

class Envelope(BaseModel):
    event_id: str
    event_type: str
    workspace_id: str
    connection_id: str
    correlation_id: str
    schema_version: int
    payload: dict

@app.post("/events")
async def receive(event: Envelope, x_hub_key: str | None = Header(default=None)):
    expected = os.environ.get("CALLBACK_SHARED_KEY", "")
    if not expected or not x_hub_key or not hmac.compare_digest(expected, x_hub_key):
        raise HTTPException(403, "Forbidden")
    log.warning("DEMO received %s (%s)", event.event_type, event.event_id)
    return {"ack": True, "event_id": event.event_id}

@app.get("/health")
async def health():
    return {"ok": True}
