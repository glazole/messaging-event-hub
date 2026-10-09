from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://eventhub:eventhub@localhost:5432/eventhub"
    admin_api_key: str = ""
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 60
    worker_batch_size: int = 10
    http_timeout_seconds: float = 12.0

settings = Settings()
