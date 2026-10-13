"""Admin/control-plane и Telegram webhook; ни одного рабочего MAX endpoint."""
import uuid
import hmac
import hashlib
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from eventhub.config import settings
from eventhub.db import Session, engine
from eventhub.models import Workspace, ChannelConnection, ServiceEndpoint, EventRoute, EventRecord, EventDelivery, ServicePublishGrant, OutboundCommand
from eventhub.core import publish, queue_outbound, resolve_secret
from eventhub.adapters.telegram import TelegramAdapter

@asynccontextmanager
async def lifespan(app):
    if not settings.admin_api_key or len(settings.admin_api_key) < 20:
        raise RuntimeError("Set ADMIN_API_KEY to at least 20 characters")
    yield
    await engine.dispose()

app = FastAPI(title="Messaging Event Hub", version="0.1.0", lifespan=lifespan)

def admin(x_admin_key: str | None = Header(default=None)):
    import hmac
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(403, "Forbidden")

class WorkspaceIn(BaseModel):
    code: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=255)

class ConnectionIn(BaseModel):
    workspace_id: uuid.UUID
    connection_key: str = Field(min_length=2, max_length=100)
    display_name: str = Field(min_length=2, max_length=255)
    provider_key: str = Field(pattern="^(telegram|max)$")
    credential_ref: str | None = None
    webhook_secret_ref: str | None = None

class ServiceIn(BaseModel):
    workspace_id: uuid.UUID
    service_key: str
    callback_url: str | None = None
    auth_secret_ref: str | None = None
    publish_secret_ref: str | None = None

class RouteIn(BaseModel):
    workspace_id: uuid.UUID
    service_id: uuid.UUID
    connection_id: uuid.UUID | None = None
    event_type: str

class PublishGrantIn(BaseModel):
    service_id: uuid.UUID
    connection_id: uuid.UUID


class MessageTarget(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=255)


class TextMessage(BaseModel):
    type: str = Field(pattern="^text$")
    text: str = Field(min_length=1, max_length=4000)


class MessageIn(BaseModel):
    connection_key: str = Field(min_length=2, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=150)
    target: MessageTarget
    message: TextMessage


class ServiceReplyIn(BaseModel):
    # Narrow-scoped business reply: one response per event/service.
    event_id: uuid.UUID
    service_key: str = Field(min_length=2, max_length=100)
    text: str = Field(min_length=1, max_length=4000)

class IngestIn(BaseModel):
    connection_id: uuid.UUID
    event_type: str
    idempotency_key: str
    payload: dict = Field(default_factory=dict)

class OutboundIn(BaseModel):
    connection_id: uuid.UUID
    command_type: str
    idempotency_key: str
    target: dict
    payload: dict

@app.get("/health/live")
async def live():
    return {"status": "ok"}

@app.get("/health/ready")
async def ready():
    from sqlalchemy import text
    async with Session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}

@app.post("/v1/workspaces", dependencies=[Depends(admin)])
async def create_workspace(body: WorkspaceIn):
    async with Session.begin() as session:
        obj = Workspace(**body.model_dump())
        session.add(obj)
        await session.flush()
        return {"id": str(obj.id)}

@app.post("/v1/connections", dependencies=[Depends(admin)])
async def create_connection(body: ConnectionIn):
    async with Session.begin() as session:
        obj = ChannelConnection(**body.model_dump())
        session.add(obj)
        await session.flush()
        return {"id": str(obj.id)}

@app.post("/v1/services", dependencies=[Depends(admin)])
async def create_service(body: ServiceIn):
    if body.callback_url is not None and not body.callback_url.startswith(("http://", "https://")):
        raise HTTPException(422, "callback_url must use http(s)")
    # Control plane is trusted; never publish it directly on the internet.
    async with Session.begin() as session:
        obj = ServiceEndpoint(**body.model_dump())
        session.add(obj)
        await session.flush()
        return {"id": str(obj.id)}

@app.post("/v1/routes", dependencies=[Depends(admin)])
async def create_route(body: RouteIn):
    async with Session.begin() as session:
        service = await session.get(ServiceEndpoint, body.service_id)
        if not service or service.workspace_id != body.workspace_id:
            raise HTTPException(422, "Service workspace mismatch")
        if body.connection_id:
            connection = await session.get(ChannelConnection, body.connection_id)
            if not connection or connection.workspace_id != body.workspace_id:
                raise HTTPException(422, "Connection workspace mismatch")
        obj = EventRoute(**body.model_dump())
        session.add(obj)
        await session.flush()
        return {"id": str(obj.id)}

@app.post("/v1/publish-grants", dependencies=[Depends(admin)])
async def create_publish_grant(body: PublishGrantIn):
    async with Session.begin() as session:
        service = await session.get(ServiceEndpoint, body.service_id)
        connection = await session.get(ChannelConnection, body.connection_id)
        if not service or not connection or service.workspace_id != connection.workspace_id:
            raise HTTPException(422, "Service/connection workspace mismatch")
        existing = (await session.execute(select(ServicePublishGrant).where(
            ServicePublishGrant.service_id == body.service_id,
            ServicePublishGrant.connection_id == body.connection_id
        ))).scalar_one_or_none()
        if existing:
            return {"id": str(existing.id), "created": False, "enabled": existing.is_enabled}
        grant = ServicePublishGrant(service_id=body.service_id, connection_id=body.connection_id)
        session.add(grant)
        await session.flush()
        return {"id": str(grant.id), "created": True, "enabled": True}


@app.post("/v1/messages", status_code=202)
async def publish_message(body: MessageIn, authorization: str | None = Header(default=None)):
    """Service-scoped outbound publication; never accepts arbitrary provider methods."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token = authorization[7:]
    if len(token) < 32 or len(token) > 1024:
        raise HTTPException(401, "Invalid bearer token")
    async with Session.begin() as session:
        # Existing MVP stores env references, never plaintext credentials.
        # Select eligible connections via explicit active grants.
        candidates = (await session.execute(
            select(ServiceEndpoint, ChannelConnection)
            .join(ServicePublishGrant, ServicePublishGrant.service_id == ServiceEndpoint.id)
            .join(ChannelConnection, ServicePublishGrant.connection_id == ChannelConnection.id)
            .where(ServicePublishGrant.is_enabled.is_(True),
                   ServiceEndpoint.is_enabled.is_(True),
                   ChannelConnection.is_enabled.is_(True),
                   ChannelConnection.connection_key == body.connection_key)
        )).all()
        authorized = []
        for service, connection in candidates:
            if not service.publish_secret_ref:
                continue
            try:
                expected = resolve_secret(service.publish_secret_ref)
            except ValueError:
                continue
            if hmac.compare_digest(token, expected):
                authorized.append((service, connection))
        if len(authorized) != 1:
            raise HTTPException(403, "Service unauthorized for connection")
        service, connection = authorized[0]
        # Namespace deduplication by publisher so independent services cannot collide.
        scoped_key = "svc:" + str(service.id) + ":" + hashlib.sha256(body.idempotency_key.encode()).hexdigest()
        existing = (await session.execute(select(OutboundCommand).where(
            OutboundCommand.connection_id == connection.id,
            OutboundCommand.idempotency_key == scoped_key
        ))).scalar_one_or_none()
        target = body.target.model_dump()
        payload = {"text": body.message.text}
        if existing:
            if (existing.source_service_id != service.id or existing.command_type != "message.send"
                    or existing.target != target or existing.payload != payload):
                raise HTTPException(409, "Idempotency key reused with different content")
            return {"command_id": str(existing.id), "created": False}
        command_id, created = await queue_outbound(
            session, connection, scoped_key, "message.send", target, payload
        )
        if created:
            cmd = await session.get(OutboundCommand, command_id)
            cmd.source_service_id = service.id
        return {"command_id": str(command_id), "created": created}


@app.post("/v1/events", dependencies=[Depends(admin)])
async def ingest_event(body: IngestIn):
    async with Session.begin() as session:
        connection = await session.get(ChannelConnection, body.connection_id)
        if not connection or not connection.is_enabled:
            raise HTTPException(404, "Unknown connection")
        eid, created = await publish(session, connection, body.event_type, body.idempotency_key, body.payload)
        return {"event_id": str(eid), "created": created}

@app.post("/v1/outbound", dependencies=[Depends(admin)])
async def enqueue_outbound(body: OutboundIn):
    async with Session.begin() as session:
        connection = await session.get(ChannelConnection, body.connection_id)
        if not connection or not connection.is_enabled:
            raise HTTPException(404, "Unknown connection")
        oid, created = await queue_outbound(session, connection, body.idempotency_key,
                                            body.command_type, body.target, body.payload)
        return {"command_id": str(oid), "created": created}

@app.post("/v1/service-replies")
async def create_service_reply(body: ServiceReplyIn, x_hub_key: str | None = Header(default=None)):
    """Allow a routed service to reply once to an event, without ADMIN_API_KEY.

    Authentication uses the service's existing callback secret (X-Hub-Key).
    The target conversation and connection are always derived from the event,
    preventing arbitrary chat_id or connection selection by a business service.
    """
    import hmac
    if not x_hub_key:
        raise HTTPException(403, "Forbidden")
    async with Session.begin() as session:
        event = await session.get(EventRecord, body.event_id)
        if not event:
            raise HTTPException(404, "Event not found")
        service = (await session.execute(
            select(ServiceEndpoint)
            .join(EventDelivery, EventDelivery.service_id == ServiceEndpoint.id)
            .where(
                EventDelivery.event_id == event.id,
                ServiceEndpoint.workspace_id == event.workspace_id,
                ServiceEndpoint.service_key == body.service_key,
                ServiceEndpoint.is_enabled.is_(True),
            )
            .limit(1)
        )).scalar_one_or_none()
        if not service:
            raise HTTPException(403, "Service has no delivery for this event")
        try:
            expected = resolve_secret(service.auth_secret_ref)
        except ValueError:
            raise HTTPException(503, "Service verification unavailable")
        if not hmac.compare_digest(expected, x_hub_key):
            raise HTTPException(403, "Forbidden")
        if event.event_type != "message.received":
            raise HTTPException(422, "Only message.received can be replied to")
        conversation_id = event.payload.get("conversation_id")
        if not conversation_id:
            raise HTTPException(422, "Event has no conversation_id")
        connection = await session.get(ChannelConnection, event.connection_id)
        if not connection or not connection.is_enabled:
            raise HTTPException(404, "Connection unavailable")
        command_id, created = await queue_outbound(
            session, connection,
            f"service-reply:{service.id}:{event.id}",
            "message.send",
            {"conversation_id": str(conversation_id)},
            {"text": body.text},
        )
        return {"command_id": str(command_id), "created": created}

@app.post("/v1/webhooks/telegram/{connection_id}")
async def telegram_webhook(connection_id: uuid.UUID, request: Request,
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    async with Session.begin() as session:
        connection = await session.get(ChannelConnection, connection_id)
        if not connection or not connection.is_enabled or connection.provider_key != "telegram":
            raise HTTPException(404, "Unknown connection")
        try:
            secret = resolve_secret(connection.webhook_secret_ref)
        except ValueError:
            raise HTTPException(503, "Webhook verification unavailable")
        if not TelegramAdapter.valid_secret(secret, x_telegram_bot_api_secret_token):
            raise HTTPException(403, "Invalid webhook credentials")
        try:
            body = await request.json()
            kind, provider_id, canonical = TelegramAdapter.normalize(body)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "Invalid Telegram update")
        eid, created = await publish(session, connection, kind, "tg:"+provider_id,
                                     canonical, body, provider_id)
        return {"ok": True, "created": created, "event_id": str(eid)}
