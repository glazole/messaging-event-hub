"""Non-destructive service publishing credentials, grants and outbound attribution.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    service_cols = {c["name"] for c in inspector.get_columns("service_endpoints", schema="relay")}
    if "callback_url" in service_cols:
        callback_column = next(c for c in inspector.get_columns("service_endpoints", schema="relay") if c["name"] == "callback_url")
        if not callback_column["nullable"]:
            op.alter_column("service_endpoints", "callback_url", schema="relay",
                            existing_type=sa.Text(), nullable=True)
    if "publish_secret_ref" not in service_cols:
        op.add_column("service_endpoints", sa.Column("publish_secret_ref", sa.String(255), nullable=True), schema="relay")
    outbound_cols = {c["name"] for c in inspector.get_columns("outbound_commands", schema="relay")}
    if "source_service_id" not in outbound_cols:
        op.add_column("outbound_commands", sa.Column("source_service_id", sa.UUID(), nullable=True), schema="relay")
    outbound_fks = {fk.get("name") for fk in inspector.get_foreign_keys("outbound_commands", schema="relay")}
    if "outbound_commands_source_service_id_fkey" not in outbound_fks:
        op.create_foreign_key("outbound_commands_source_service_id_fkey", "outbound_commands", "service_endpoints",
                              ["source_service_id"], ["id"], source_schema="relay", referent_schema="relay")
    if not inspector.has_table("service_publish_grants", schema="relay"):
        op.create_table(
            "service_publish_grants",
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("service_id", sa.UUID(), sa.ForeignKey("relay.service_endpoints.id"), nullable=False),
            sa.Column("connection_id", sa.UUID(), sa.ForeignKey("relay.channel_connections.id"), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("service_id", "connection_id"),
            schema="relay",
        )


def downgrade():
    # Intentional: rolling back must not silently discard grants or attribution.
    raise RuntimeError("0002 is data-preserving; use an explicit reviewed migration to reverse it")
