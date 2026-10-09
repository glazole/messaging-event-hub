from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from eventhub.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)
