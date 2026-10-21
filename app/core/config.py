from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from app.core.vapid import validate_key_pair


class Settings(BaseSettings):
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    auth_session_ttl_days: int
    vapid_private_key: SecretStr | None = None
    vapid_public_key: str | None = None
    vapid_subject: str | None = None
    push_ttl_seconds: int = Field(default=3600, ge=1, le=3600)
    push_timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    push_max_concurrency: int = Field(default=10, ge=1, le=100)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_vapid(self) -> Self:
        if all(
            value is None
            for value in (
                self.vapid_private_key,
                self.vapid_public_key,
                self.vapid_subject,
            )
        ):
            return self
        if (
            self.vapid_private_key is None
            or self.vapid_public_key is None
            or self.vapid_subject is None
        ):
            raise ValueError("All VAPID settings must be provided")
        validate_key_pair(
            self.vapid_private_key.get_secret_value(), self.vapid_public_key
        )
        subject = urlsplit(self.vapid_subject)
        if subject.scheme == "mailto":
            valid = (
                "@" in subject.path
                and not subject.netloc
                and not subject.query
                and not subject.fragment
            )
        else:
            valid = (
                subject.scheme == "https"
                and subject.hostname is not None
                and subject.username is None
                and subject.password is None
            )
        if not valid or any(char.isspace() for char in self.vapid_subject):
            raise ValueError("VAPID subject must be a mailto or HTTPS contact URL")
        return self

    def require_vapid(self) -> None:
        if self.vapid_private_key is None:
            raise ValueError("VAPID is not configured")

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]


settings = get_settings()
