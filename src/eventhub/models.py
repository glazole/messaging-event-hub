import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, Index, Text, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now():
    return datetime.now(timezone.utc)

def uid():
    return uuid.uuid4()

class Base(DeclarativeBase):
    pass

class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = {"schema": "relay"}
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ChannelConnection(Base):
    __tablename__ = "channel_connections"
    __table_args__ = (UniqueConstraint("workspace_id", "connection_key"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.workspaces.id"))
    connection_key: Mapped[str] = mapped_column(String(100))
    provider_key: Mapped[str] = mapped_column(String(60))
    display_name: Mapped[str] = mapped_column(String(255))
    credential_ref: Mapped[str | None] = mapped_column(String(255))
    webhook_secret_ref: Mapped[str | None] = mapped_column(String(255))
    external_account_id: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class ServiceEndpoint(Base):
    __tablename__ = "service_endpoints"
    __table_args__ = (UniqueConstraint("workspace_id", "service_key"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.workspaces.id"))
    service_key: Mapped[str] = mapped_column(String(100))
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_secret_ref: Mapped[str | None] = mapped_column(String(255))
    # Separate credential for service-initiated API calls; no plaintext tokens in DB.
    publish_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)

class EventRoute(Base):
    __tablename__ = "event_routes"
    __table_args__ = (UniqueConstraint("workspace_id","service_id","connection_id","event_type"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.workspaces.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.service_endpoints.id"))
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("relay.channel_connections.id"))
    event_type: Mapped[str] = mapped_column(String(150))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class ServicePublishGrant(Base):
    """Explicit authorization for a service to publish through a connection."""
    __tablename__ = "service_publish_grants"
    __table_args__ = (UniqueConstraint("service_id", "connection_id"), {"schema": "relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.service_endpoints.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.channel_connections.id"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EventRecord(Base):
    __tablename__ = "event_log"
    __table_args__ = (UniqueConstraint("connection_id","idempotency_key"),Index("ix_event_log_received","received_at"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.workspaces.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.channel_connections.id"))
    event_type: Mapped[str] = mapped_column(String(150))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uid)
    provider_event_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSONB)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class EventDelivery(Base):
    __tablename__ = "event_deliveries"
    __table_args__ = (UniqueConstraint("event_id","route_id"),Index("ix_event_delivery_claim","status","available_at"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.event_log.id"))
    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.event_routes.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.service_endpoints.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class OutboundCommand(Base):
    __tablename__ = "outbound_commands"
    __table_args__ = (UniqueConstraint("connection_id","idempotency_key"),Index("ix_outbound_claim","status","available_at"),{"schema":"relay"})
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.workspaces.id"))
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relay.channel_connections.id"))
    # Nullable: all legacy outbound rows predate service identities.
    source_service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("relay.service_endpoints.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uid)
    command_type: Mapped[str] = mapped_column(String(100))
    target: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
