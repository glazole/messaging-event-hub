"""Initial independent event hub schema.

Revision ID: 0001
Revises:
"""
from alembic import op
from eventhub.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS relay")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP SCHEMA IF EXISTS relay")
