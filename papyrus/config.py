from functools import lru_cache
from pathlib import Path

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    debug: bool = False
    host: str
    port: int
    api_prefix: str
    cors_origins: list[str]
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    rate_limit_auth: int
    rate_limit_general: int
    rate_limit_upload: int
    rate_limit_batch: int
    public_base_url: str | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    oauth_state_expire_minutes: int = 10
    auth_exchange_code_expire_minutes: int = 5
    email_verification_token_expire_minutes: int = 1440
    password_reset_token_expire_minutes: int = 60
    email_delivery_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_from_email: str | None = None
    smtp_from_name: str | None = "Papyrus"
    powersync_jwt_private_key: str | None = None
    powersync_jwt_private_key_file: str | None = None
    powersync_jwt_public_key: str | None = None
    powersync_jwt_public_key_file: str | None = None
    powersync_jwt_key_id: str = "papyrus-powersync-v1"
    powersync_jwt_audience: str | None = None
    powersync_token_expire_minutes: int = 5
    powersync_service_url: str = "http://localhost:8081"
    powersync_service_port: int = 8081
    powersync_jwks_uri: str | None = None
    powersync_source_role: str | None = None
    powersync_source_password: str | None = None
    powersync_storage_db: str | None = None
    powersync_storage_user: str | None = None
    powersync_storage_password: str | None = None

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, value: bool | str) -> bool:
        if isinstance(value, bool):
            return value

        normalized = value.strip().lower()
        truthy = {"1", "true", "yes", "on", "debug", "development", "dev"}
        falsy = {"0", "false", "no", "off", "release", "production", "prod"}

        if normalized in truthy:
            return True

        if normalized in falsy:
            return False

        raise ValueError("debug must be a boolean-compatible value")

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        normalized = value.strip()

        if not normalized or normalized == "/":
            return ""

        if not normalized.startswith("/"):
            normalized = f"/{normalized}"

        return normalized.rstrip("/")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def powersync_jwt_private_key_path(self) -> Path | None:
        if self.powersync_jwt_private_key_file is None:
            return None

        return Path(self.powersync_jwt_private_key_file)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def powersync_jwt_public_key_path(self) -> Path | None:
        if self.powersync_jwt_public_key_file is None:
            return None

        return Path(self.powersync_jwt_public_key_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
