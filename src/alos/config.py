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
    OTEL_SERVICE_NAME: str = "alos-backend"
    ALOS_CONTRACTS_PATH: Path | None = None
    ENABLE_TEST_TOOLS: bool = False

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

    @field_validator("ENABLE_TEST_TOOLS")
    @classmethod
    def forbid_test_tools_in_production(cls, value: bool, info: object) -> bool:
        data = getattr(info, "data", {})
        if value and data.get("APP_ENV") == "production":
            raise ValueError("ENABLE_TEST_TOOLS cannot be enabled in production")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
