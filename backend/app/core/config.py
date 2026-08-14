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
    upload_ssh_host: str = "192.168.1.213"
    upload_ssh_port: int = 22
    fixed_account_password: str = ""
    auth_session_secret: str = ""
    auth_session_ttl_seconds: int = 43200
    account_invitation_ttl_seconds: int = 604800
    file_access_ttl_seconds: int = 120
    upload_ticket_ttl_seconds: int = 86400
    agent_upload_max_bytes: int = 500_000_000
    upload_destination_root: str = "/home/zhengyu/SageDataManager/sample-archive"
    update_agent_socket: Path = Path("/run/sage-updater/updater.sock")
    update_agent_secret: str = ""
    update_agent_timeout_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAGE_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
