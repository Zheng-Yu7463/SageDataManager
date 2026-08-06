from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SAGE Data Manager"
    environment: str = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://sage:sage@localhost:5432/sage"
    allowed_origins: list[str] = ["http://localhost:5173"]
    storage_root: Path = Path("/data/sage-archive")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAGE_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
