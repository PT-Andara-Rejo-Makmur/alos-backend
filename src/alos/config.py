"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Secret values are represented as SecretStr and never serialized."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
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
    AUTH_SESSION_TTL_MINUTES: int = Field(default=480, ge=5, le=43200)
    EGRESS_ALLOWED_PROTOCOLS: str = "https"
    EGRESS_ALLOWED_DOMAINS: str = ""
    EGRESS_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=120)
    EGRESS_MAX_RESPONSE_BYTES: int = Field(default=1_000_000, gt=0, le=50_000_000)
    EGRESS_ALLOWED_CONTENT_TYPES: str = "application/json,text/plain,text/html,application/xml"
    EGRESS_BLOCK_PRIVATE_NETWORKS: bool = True

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
