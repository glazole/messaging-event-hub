import os
import uuid
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from eventhub.models import EventRecord, EventRoute, EventDelivery, OutboundCommand, ChannelConnection

def resolve_secret(reference: str | None) -> str:
    """Только env://NAME. Никаких секретов в БД или в логах."""
    if not reference or not reference.startswith("env://"):
        raise ValueError("Required secret reference env://NAME is missing")
    key = reference[6:]
    if not key or not key.replace("_", "").isalnum():
        raise ValueError("Invalid secret name")
    value = os.environ.get(key, "")
    if not value:
        raise ValueError("Secret variable is not configured")
    return value

async def publish(session, connection: ChannelConnection, event_type: str, dedup: str,
                  payload: dict, raw: dict | None = None, provider_event_id: str | None = None):
    if not event_type or len(dedup) > 255:
        raise ValueError("Invalid event")
    event_id = uuid.uuid4()
    stmt = pg_insert(EventRecord).values(
        id=event_id, workspace_id=connection.workspace_id, connection_id=connection.id,
        event_type=event_type, idempotency_key=dedup, payload=payload,
        raw_payload=raw, provider_event_id=provider_event_id
    ).on_conflict_do_nothing(index_elements=["connection_id","idempotency_key"]).returning(EventRecord.id)
    created_id = (await session.execute(stmt)).scalar_one_or_none()
    if not created_id:
        existing = (await session.execute(select(EventRecord.id).where(
            EventRecord.connection_id == connection.id, EventRecord.idempotency_key == dedup))).scalar_one()
        return existing, False
    routes = (await session.execute(select(EventRoute).where(
        EventRoute.workspace_id == connection.workspace_id,
        EventRoute.is_enabled.is_(True),
        EventRoute.event_type.in_([event_type, "*"]),
        (EventRoute.connection_id == connection.id) | (EventRoute.connection_id.is_(None))
    ))).scalars().all()
    for route in routes:
        session.add(EventDelivery(event_id=created_id, route_id=route.id, service_id=route.service_id))
    await session.flush()
    return created_id, True

async def queue_outbound(session, connection: ChannelConnection, dedup: str, command_type: str,
                         target: dict, payload: dict):
    new_id = uuid.uuid4()
    stmt = pg_insert(OutboundCommand).values(
        id=new_id, workspace_id=connection.workspace_id, connection_id=connection.id,
        idempotency_key=dedup, command_type=command_type, target=target, payload=payload
    ).on_conflict_do_nothing(index_elements=["connection_id","idempotency_key"]).returning(OutboundCommand.id)
    value = (await session.execute(stmt)).scalar_one_or_none()
    if value:
        return value, True
    old = (await session.execute(select(OutboundCommand.id).where(
        OutboundCommand.connection_id == connection.id, OutboundCommand.idempotency_key == dedup))).scalar_one()
    return old, False
