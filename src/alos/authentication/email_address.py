"""Canonical syntax validation and normalization for identity email addresses."""

from email_validator import EmailNotValidError, validate_email


def normalize_email(value: str | None) -> str:
    """Validate syntax without network/DNS checks; support deterministic .test fixtures."""
    if not value or not value.strip():
        raise ValueError("employee email is required")
    try:
        normalized = validate_email(
            value.strip().lower(), check_deliverability=False, test_environment=True
        ).normalized
    except EmailNotValidError as exc:
        raise ValueError("employee email is invalid") from exc
    if len(normalized) > 255:
        raise ValueError("employee email is invalid")
    return normalized


def valid_email(value: str | None) -> bool:
    try:
        normalize_email(value)
    except ValueError:
        return False
    return True
