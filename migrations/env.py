import asyncio
from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config
from eventhub.config import settings
from eventhub.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
target_metadata = Base.metadata

def do_run_migrations(connection):
    # Alembic creates its version table *before* upgrade() executes.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS relay"))
    connection.commit()  # Finish implicit DDL transaction before Alembic owns transaction.
    context.configure(connection=connection, target_metadata=target_metadata,
                      include_schemas=True, version_table_schema="relay")
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    connectable = async_engine_from_config(config.get_section(config.config_ini_section),
                                          prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

asyncio.run(run_async_migrations())
