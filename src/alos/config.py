"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from alos.authentication.email_address import normalize_email, valid_email


class Settings(BaseSettings):
    """Runtime settings. Secret values are represented as SecretStr and never serialized."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    APP_ENV: Literal["development", "test", "staging", "production"] = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    DATABASE_URL: str = "postgresql+asyncpg://alos:alos@localhost:5432/alos"
    GENESIS_BASE_URL: str = "http://localhost:8100"
    GENESIS_INTERNAL_TOKEN: SecretStr = SecretStr("")
    CORS_ALLOWED_ORIGINS: str = "http://127.0.0.1:3000,http://localhost:3000"
    OTEL_SERVICE_NAME: str = "alos-backend"
    ALOS_CONTRACTS_PATH: Path | None = None
    ENABLE_TEST_TOOLS: bool = False
    ENABLE_TEST_REGISTRATION: bool = False
    DOCUMENT_OBJECT_ROOT: Path = Path("storage/documents")
    DOCUMENT_UPLOAD_MAX_BYTES: int = Field(default=10_000_000, ge=1, le=25_000_000)
    AUTH_SESSION_TTL_MINUTES: int = Field(default=480, ge=5, le=43200)
    EMAIL_PROVIDER: Literal["smtp", "test", "sink", "memory", "inmemory"] = "smtp"
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "ALOS"
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("SMTP_PASSWORD", "SMTP_APP_PASSWORD"),
    )
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=120)
    APP_PUBLIC_URL: str = ""
    ACTIVATION_TTL_HOURS: int = Field(default=24, ge=1, le=168)
    PASSWORD_RESET_TTL_MINUTES: int = Field(default=60, ge=5, le=1440)
    EGRESS_ALLOWED_PROTOCOLS: str = "https"
    EGRESS_ALLOWED_DOMAINS: str = ""
    EGRESS_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=120)
    EGRESS_MAX_RESPONSE_BYTES: int = Field(default=1_000_000, gt=0, le=50_000_000)
    EGRESS_ALLOWED_CONTENT_TYPES: str = "application/json,text/plain,text/html,application/xml"
    EGRESS_BLOCK_PRIVATE_NETWORKS: bool = True

    @property
    def is_email_configured(self) -> bool:
        """Indicate whether email dispatch is available without exposing credentials."""
        if self.EMAIL_PROVIDER in {"test", "sink", "memory", "inmemory"}:
            return self.APP_ENV in {"development", "test"}
        return bool(
            valid_email(self.EMAIL_FROM)
            and self.EMAIL_FROM_NAME
            and self.SMTP_HOST
            and self.SMTP_USERNAME
            and self.SMTP_PASSWORD.get_secret_value().strip()
            and self.APP_PUBLIC_URL
        )

    @field_validator("EMAIL_FROM", "EMAIL_FROM_NAME", "SMTP_HOST", "SMTP_USERNAME")
    @classmethod
    def trim_email_configuration(cls, value: str) -> str:
        return value.strip()

    @field_validator("EMAIL_FROM")
    @classmethod
    def validate_sender(cls, value: str) -> str:
        return normalize_email(value) if value else ""

    @field_validator("APP_PUBLIC_URL")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        if not value.strip():
            return ""
        url = TypeAdapter(AnyHttpUrl).validate_python(value.strip())
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("APP_PUBLIC_URL must be an HTTP(S) base URL without credentials")
        return str(url).rstrip("/")

    @model_validator(mode="after")
    def require_deployed_smtp_configuration(self) -> Settings:
        if self.APP_ENV not in {"staging", "production"}:
            return self
        if self.EMAIL_PROVIDER != "smtp":
            raise ValueError("Staging and production require EMAIL_PROVIDER=smtp")
        required = {
            "EMAIL_FROM": self.EMAIL_FROM,
            "EMAIL_FROM_NAME": self.EMAIL_FROM_NAME,
            "SMTP_HOST": self.SMTP_HOST,
            "SMTP_PORT": self.SMTP_PORT,
            "SMTP_USERNAME": self.SMTP_USERNAME,
            "SMTP_PASSWORD": self.SMTP_PASSWORD.get_secret_value().strip(),
            "APP_PUBLIC_URL": self.APP_PUBLIC_URL,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"SMTP configuration requires: {', '.join(missing)}")
        if "://" in self.SMTP_HOST or any(char.isspace() for char in self.SMTP_HOST):
            raise ValueError("SMTP_HOST must be a hostname or IP address")
        host = (
            (TypeAdapter(AnyHttpUrl).validate_python(self.APP_PUBLIC_URL).host or "")
            .lower()
            .rstrip(".")
        )
        if self.APP_ENV == "production":
            local = host.lower().rstrip(".") == "localhost" or host.endswith(".localhost")
            try:
                local = local or ip_address(host.strip("[]")).is_loopback
            except ValueError:
                pass
            if local:
                raise ValueError("APP_PUBLIC_URL cannot use localhost in production")
        return self

    @field_validator("DATABASE_URL")
    @classmethod
    def require_async_database_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value

    @field_validator("GENESIS_BASE_URL")
    @classmethod
    def normalize_genesis_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Return explicitly configured browser origins for the public API."""
        return [
            origin.strip().rstrip("/")
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @staticmethod
    def _csv(value: str) -> frozenset[str]:
        return frozenset(item.strip().lower() for item in value.split(",") if item.strip())

    @property
    def egress_allowed_protocols(self) -> frozenset[str]:
        return self._csv(self.EGRESS_ALLOWED_PROTOCOLS)

    @property
    def egress_allowed_domains(self) -> frozenset[str]:
        return self._csv(self.EGRESS_ALLOWED_DOMAINS)

    @property
    def egress_allowed_content_types(self) -> frozenset[str]:
        return self._csv(self.EGRESS_ALLOWED_CONTENT_TYPES)

    @field_validator("ENABLE_TEST_TOOLS")
    @classmethod
    def forbid_test_tools_in_production(cls, value: bool, info: object) -> bool:
        data = getattr(info, "data", {})
        if value and data.get("APP_ENV") == "production":
            raise ValueError("ENABLE_TEST_TOOLS cannot be enabled in production")
        return value

    @field_validator("ENABLE_TEST_REGISTRATION")
    @classmethod
    def forbid_test_registration_in_production(cls, value: bool, info: object) -> bool:
        data = getattr(info, "data", {})
        if value and data.get("APP_ENV") in {"staging", "production"}:
            raise ValueError("ENABLE_TEST_REGISTRATION cannot be enabled outside development/test")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
