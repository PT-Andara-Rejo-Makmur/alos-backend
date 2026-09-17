import pytest
from pydantic import ValidationError

from alos.config import Settings


def test_configuration_accepts_typed_values() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        APP_PORT=9000,
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/alos",
    )
    assert settings.APP_PORT == 9000
    assert settings.APP_ENV == "test"


def test_configuration_rejects_non_async_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DATABASE_URL="postgresql://user:pass@localhost/alos")


def test_configuration_forbids_test_tools_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            ENABLE_TEST_TOOLS=True,
        )
