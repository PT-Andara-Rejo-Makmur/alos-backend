from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from alos.config import Settings
from alos.notifications.models import EmailMessage
from alos.notifications.service import NotificationService
from alos.notifications.smtp_adapter import InMemoryEmailAdapter, SmtpEmailAdapter

SMTP_CONFIG = {
    "EMAIL_PROVIDER": "smtp",
    "EMAIL_FROM": "notification@company-domain.com",
    "EMAIL_FROM_NAME": "ALOS",
    "SMTP_HOST": "mail.company-domain.com",
    "SMTP_PORT": 587,
    "SMTP_USERNAME": "smtp-user",
    "SMTP_PASSWORD": "test-only-secret",
    "APP_PUBLIC_URL": "https://app.company-domain.com",
}


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize(
    "missing",
    [
        "EMAIL_FROM",
        "EMAIL_FROM_NAME",
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "APP_PUBLIC_URL",
    ],
)
def test_deployed_smtp_fails_closed(environment, missing) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV=environment, **{**SMTP_CONFIG, missing: "   "})


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EMAIL_FROM", "invalid@@company-domain.com"),
        ("APP_PUBLIC_URL", "not-a-url"),
        ("APP_PUBLIC_URL", "ftp://company-domain.com"),
        ("SMTP_PORT", 0),
        ("SMTP_PORT", 65536),
        ("SMTP_HOST", "https://mail.company-domain.com"),
        ("SMTP_HOST", "mail host"),
    ],
)
def test_deployed_smtp_rejects_invalid_values(environment, key, value) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV=environment, **{**SMTP_CONFIG, key: value})


@pytest.mark.parametrize(
    "url", ["http://localhost:3000", "http://127.0.0.1", "http://[::1]", "http://app.localhost"]
)
def test_production_rejects_loopback_public_url(url) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="production", **{**SMTP_CONFIG, "APP_PUBLIC_URL": url})


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("provider", ["inmemory", "test", "sink", "memory"])
def test_inmemory_needs_no_smtp_configuration(environment, provider) -> None:
    settings = Settings(_env_file=None, APP_ENV=environment, EMAIL_PROVIDER=provider)
    service = NotificationService(settings)
    assert settings.is_email_configured
    assert isinstance(service._adapter, InMemoryEmailAdapter)


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("provider", ["inmemory", "test", "sink", "memory"])
def test_deployed_environments_reject_test_providers(environment, provider) -> None:
    with pytest.raises(ValidationError, match="require EMAIL_PROVIDER=smtp"):
        Settings(_env_file=None, APP_ENV=environment, **{**SMTP_CONFIG, "EMAIL_PROVIDER": provider})
    # Readiness must not bless a deployed test adapter even when validation is bypassed.
    settings = Settings.model_construct(APP_ENV=environment, EMAIL_PROVIDER=provider)
    assert not settings.is_email_configured


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_custom_smtp_and_sender_accepted_without_vendor_requirement(environment) -> None:
    settings = Settings(_env_file=None, APP_ENV=environment, **SMTP_CONFIG)
    assert settings.is_email_configured
    assert settings.SMTP_HOST == "mail.company-domain.com"
    assert NotificationService(settings)._from_email == "notification@company-domain.com"


def test_no_smtp_host_or_dummy_sender_default() -> None:
    settings = Settings(_env_file=None, EMAIL_PROVIDER="smtp")
    assert settings.SMTP_HOST == ""
    assert not settings.is_email_configured
    assert NotificationService(settings)._from_email == ""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            APP_ENV="production",
            **{**SMTP_CONFIG, "EMAIL_FROM": "notification@alos.local"},
        )


def test_legacy_password_alias_and_canonical_precedence(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_APP_PASSWORD", "legacy-test-value")
    assert Settings(_env_file=None).SMTP_PASSWORD.get_secret_value() == "legacy-test-value"
    monkeypatch.setenv("SMTP_PASSWORD", "canonical-test-value")
    assert Settings(_env_file=None).SMTP_PASSWORD.get_secret_value() == "canonical-test-value"


def test_invalid_startup_does_not_print_smtp_secret() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, APP_ENV="production", **{**SMTP_CONFIG, "SMTP_HOST": ""})
    assert SMTP_CONFIG["SMTP_PASSWORD"] not in str(error.value)


@pytest.mark.parametrize("port", [587, 465])
async def test_generic_transport_authenticates_and_sends_with_tls(port) -> None:
    settings = Settings(_env_file=None, APP_ENV="production", **{**SMTP_CONFIG, "SMTP_PORT": port})
    server = MagicMock()
    with (
        patch("alos.notifications.smtp_adapter.smtplib.SMTP", return_value=server) as smtp,
        patch("alos.notifications.smtp_adapter.smtplib.SMTP_SSL", return_value=server) as smtp_ssl,
    ):
        result = await SmtpEmailAdapter(settings).send(
            EmailMessage(
                to_email="employee@company-domain.com",
                subject="Invitation",
                text_content="text",
                html_content="<p>text</p>",
                from_name="ALOS",
                from_email=settings.EMAIL_FROM,
            )
        )
    assert result.success
    selected = smtp_ssl if port == 465 else smtp
    assert selected.call_args.kwargs["host"] == settings.SMTP_HOST
    server.login.assert_called_once_with("smtp-user", "test-only-secret")
    assert server.starttls.call_count == (0 if port == 465 else 1)
    server.send_message.assert_called_once()
