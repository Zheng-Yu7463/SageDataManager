from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_name: str = "SAGE Data Manager"
    environment: str = "development"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://sage:sage@localhost:5432/sage"
    database_host: str | None = Field(default=None, min_length=1)
    database_port: int | None = Field(default=None, ge=1, le=65_535)
    database_name: str | None = Field(default=None, min_length=1)
    database_user: str | None = Field(default=None, min_length=1)
    database_password: str | None = Field(default=None, min_length=1)
    allowed_origins: list[str] = ["http://localhost:5173"]
    storage_root: Path = Path("/data/sage-archive")
    upload_ssh_host: str = "192.168.1.213"
    upload_ssh_port: int = Field(default=22, ge=1, le=65_535)
    fixed_account_password: str = ""
    auth_session_secret: str = ""
    auth_session_ttl_seconds: int = Field(default=43_200, gt=0)
    account_invitation_ttl_seconds: int = Field(default=604_800, gt=0)
    file_access_ttl_seconds: int = Field(default=120, gt=0)
    upload_ticket_ttl_seconds: int = Field(default=86_400, gt=0)
    agent_upload_max_bytes: int = Field(default=500_000_000, gt=0, le=500_000_000)
    agent_token_last_used_interval_seconds: int = Field(default=300, ge=0)
    upload_destination_root: str = "/home/zhengyu/SageDataManager/sample-archive"
    update_agent_socket: Path = Path("/run/sage-updater/updater.sock")
    update_agent_secret: str = ""
    update_agent_timeout_seconds: int = Field(default=60, gt=0)
    release_commit: str = "unknown"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAGE_",
        extra="ignore",
    )

    @model_validator(mode="after")
    def build_database_url_from_components(self) -> "Settings":
        components = {
            "database_host": self.database_host,
            "database_port": self.database_port,
            "database_name": self.database_name,
            "database_user": self.database_user,
            "database_password": self.database_password,
        }
        if not any(value is not None for value in components.values()):
            return self
        missing = [name for name, value in components.items() if value is None]
        if missing:
            raise ValueError(
                "Database connection components must be configured together; missing: "
                + ", ".join(missing)
            )
        self.database_url = URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=self.database_password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
