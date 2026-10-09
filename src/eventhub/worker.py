"""Независимые delivery/outbound циклы. PostgreSQL является durable queue."""
import asyncio
import logging
import os
import socket
from datetime import timedelta
import httpx
from sqlalchemy import select, or_, and_
from eventhub.config import settings
from eventhub.db import Session, engine
from eventhub.models import EventDelivery, EventRecord, ServiceEndpoint, OutboundCommand, ChannelConnection, now
from eventhub.core import resolve_secret
from eventhub.adapters.telegram import TelegramAdapter

logging.basicConfig(level=logging.INFO)
# HTTPX logs complete request URLs at INFO. Telegram Bot API puts the token
# in the URL path, so those logs MUST remain disabled.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("eventhub.worker")


def redact_secret(value: str, secret: str | None) -> str:
    """Prevent Telegram credentials being persisted in last_error on failures."""
    return value.replace(secret, "[REDACTED]") if secret else value

WORKER_ID = socket.gethostname() + "-" + str(os.getpid())

async def claim(model):
    async with Session.begin() as db:
        rows = (await db.execute(
            select(model).where(
                or_(
                    and_(model.status.in_(["pending","retry"]), model.available_at <= now()),
                    and_(model.status == "processing", model.lease_until < now())
                )
            ).order_by(model.available_at, model.id).limit(settings.worker_batch_size)
            .with_for_update(skip_locked=True)
        )).scalars().all()
        ids = []
        for row in rows:
            row.status = "processing"
            row.lease_owner = WORKER_ID
            row.lease_until = now() + timedelta(seconds=settings.worker_lease_seconds)
            row.attempts += 1
            ids.append(row.id)
    return ids

async def finish(model, item_id, success: bool, error: str = "", provider_message_id: str | None = None,
                 permanent: bool = False):
    async with Session.begin() as db:
        row = await db.get(model, item_id, with_for_update=True)
        if not row or row.lease_owner != WORKER_ID or row.status != "processing":
            log.warning("Lost lease for %s", item_id)
            return
        row.lease_until = None
        row.lease_owner = None
        if success:
            row.status = "succeeded"
            if isinstance(row, EventDelivery):
                row.completed_at = now()
            elif isinstance(row, OutboundCommand):
                row.sent_at = now()
                row.provider_message_id = provider_message_id
            row.last_error = None
        else:
            row.last_error = error[:2000]
            if permanent or row.attempts >= row.max_attempts:
                row.status = "dead"
            else:
                row.status = "retry"
                row.available_at = now() + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))

async def deliver(item_id):
    try:
        async with Session() as db:
            delivery = await db.get(EventDelivery, item_id)
            endpoint = await db.get(ServiceEndpoint, delivery.service_id)
            event = await db.get(EventRecord, delivery.event_id)
            if not endpoint or not endpoint.is_enabled or not event or endpoint.workspace_id != event.workspace_id:
                raise ValueError("Inactive/missing endpoint or workspace mismatch")
            headers = {"X-Event-Id": str(event.id), "X-Delivery-Id": str(item_id)}
            if endpoint.auth_secret_ref:
                headers["X-Hub-Key"] = resolve_secret(endpoint.auth_secret_ref)
            envelope = {"event_id": str(event.id), "event_type": event.event_type,
                        "workspace_id": str(event.workspace_id), "connection_id": str(event.connection_id),
                        "correlation_id": str(event.correlation_id), "schema_version": event.schema_version,
                        "payload": event.payload, "received_at": event.received_at.isoformat()}
            callback_url = endpoint.callback_url
            timeout = endpoint.timeout_seconds
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(callback_url, json=envelope, headers=headers)
            response.raise_for_status()
        await finish(EventDelivery, item_id, True)
    except Exception as ex:
        permanent = isinstance(ex, (ValueError, httpx.HTTPStatusError)) and (
            not isinstance(ex, httpx.HTTPStatusError) or
            (400 <= ex.response.status_code < 500 and ex.response.status_code != 429))
        log.warning("Delivery %s failed: %s", item_id, type(ex).__name__)
        await finish(EventDelivery, item_id, False, str(ex), permanent=permanent)

async def send(item_id):
    token = None
    try:
        async with Session() as db:
            cmd = await db.get(OutboundCommand, item_id)
            conn = await db.get(ChannelConnection, cmd.connection_id)
            if not conn or not conn.is_enabled or conn.workspace_id != cmd.workspace_id:
                raise ValueError("Inactive/missing connection or workspace mismatch")
            if conn.provider_key != "telegram":
                raise ValueError("Outbound adapter is not implemented for: "+conn.provider_key)
            token = resolve_secret(conn.credential_ref)
            command_type, target, payload = cmd.command_type, cmd.target, cmd.payload
        result = await TelegramAdapter.send(token, command_type, target, payload)
        message_id = str(result.get("message_id")) if isinstance(result, dict) and result.get("message_id") is not None else None
        await finish(OutboundCommand, item_id, True, provider_message_id=message_id)
    except Exception as ex:
        permanent = isinstance(ex, (ValueError, httpx.HTTPStatusError)) and (
            not isinstance(ex, httpx.HTTPStatusError) or
            (400 <= ex.response.status_code < 500 and ex.response.status_code != 429))
        log.warning("Outbound %s failed: %s", item_id, type(ex).__name__)
        await finish(OutboundCommand, item_id, False,
                     redact_secret(str(ex), token), permanent=permanent)

async def run():
    if settings.worker_lease_seconds <= settings.http_timeout_seconds * 2:
        raise RuntimeError("Worker lease must exceed twice HTTP timeout")
    while True:
        try:
            d = await claim(EventDelivery)
            o = await claim(OutboundCommand)
            await asyncio.gather(*(deliver(i) for i in d), *(send(i) for i in o))
            if not d and not o:
                await asyncio.sleep(settings.worker_poll_seconds)
        except Exception:
            log.exception("Worker loop failed")
            await asyncio.sleep(3)

if __name__ == "__main__":
    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())
