import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.service import AuthService
from alos.config import Settings
from alos.notifications.models import DeliveryResult, EmailMessage
from alos.notifications.service import NotificationService
from alos.notifications.smtp_adapter import InMemoryEmailAdapter
from alos.security.errors import PlatformError


@pytest.fixture
async def authority() -> tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter]:
    repository = InMemoryAuthRepository()
    adapter = InMemoryEmailAdapter()
    service = AuthService(
        repository,
        notification_service=NotificationService(Settings(EMAIL_PROVIDER="inmemory"), adapter),
    )
    await service.bootstrap_initial_admin(
        {
            "email": "operator@example.test",
            "password": "OperatorPass!123",
            "display_name": "Operator",
            "tenant_id": "tenant_test",
            "organization_id": "org_test",
            "workspace_id": "workspace_test",
            "workspace_type": "IT_OPERATIONS",
        }
    )
    return service, repository, adapter


def employee(**overrides: object) -> dict:
    return {
        "employee_id": "employee_test",
        "employee_number": "EMP-001",
        "full_name": "Employee",
        "email": "  Employee@Example.Test  ",
        "tenant_id": "tenant_test",
        "organization_id": "org_test",
        "workspace_id": "workspace_test",
        "join_date": datetime.now(UTC).date().isoformat(),
        **overrides,
    }


def provision(**overrides: object) -> dict:
    return {
        "employee_id": "employee_test",
        "tenant_id": "tenant_test",
        "organization_id": "org_test",
        "workspace_id": "workspace_test",
        "role_refs": ["DIVISION_MEMBER"],
        **overrides,
    }


@pytest.mark.parametrize(
    "email", [None, "", "  ", "user", "user@@example.com", "user name@example.com"]
)
async def test_import_requires_valid_email(authority, email) -> None:
    service, _, _ = authority
    with pytest.raises(PlatformError, match="email") as error:
        await service.import_employee(employee(email=email))
    assert error.value.code == "INVALID_EMPLOYEE_EMAIL"


async def test_import_normalizes_and_rejects_duplicate_email(authority) -> None:
    service, _, _ = authority
    imported = await service.import_employee(employee())
    assert imported["email"] == "employee@example.test"
    with pytest.raises(PlatformError) as error:
        await service.import_employee(
            employee(employee_id="employee_other", employee_number="EMP-002")
        )
    assert error.value.code == "EMPLOYEE_CONFLICT"


async def test_provision_derives_employee_email_and_rejects_client_override(authority) -> None:
    service, repository, adapter = authority
    await service.import_employee(employee())
    with pytest.raises(PlatformError) as error:
        await service.provision(provision(email="override@example.test"))
    assert error.value.code == "CLIENT_EMAIL_FORBIDDEN"
    assert repository._employees["employee_test"]["actor_id"] is None
    account = await service.provision(provision())
    assert account["email"] == "employee@example.test"
    assert adapter.sent_messages[0].to_email == account["email"]
    assert account["activation_state"] == "PENDING"


@pytest.mark.parametrize("boundary", ["tenant_id", "organization_id"])
async def test_cross_boundary_employee_is_denied(authority, boundary) -> None:
    service, repository, _ = authority
    await service.import_employee(employee())
    repository._employees["employee_test"][boundary] = "foreign"
    assert (
        await service.provisioning_candidates(tenant_id="tenant_test", organization_id="org_test")
        == []
    )
    with pytest.raises(PlatformError):
        await service.provision(provision())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", None),
        ("email", ""),
        ("email", "bad@@example.com"),
        ("join_date", None),
        pytest.param("join_date", timedelta(days=2), id="future-join-date"),
        pytest.param("end_date", timedelta(days=-1), id="past-end-date"),
        ("employment_status", "INACTIVE"),
        ("actor_id", "actor_existing"),
    ],
)
async def test_candidates_and_provision_revalidate_legacy_employee(authority, field, value) -> None:
    service, repository, _ = authority
    await service.import_employee(employee())
    # Resolve date offsets during execution, not before a long migrated suite.
    if isinstance(value, timedelta):
        value = (datetime.now(UTC).date() + value).isoformat()
    repository._employees["employee_test"][field] = value
    assert (
        await service.provisioning_candidates(tenant_id="tenant_test", organization_id="org_test")
        == []
    )
    with pytest.raises(PlatformError):
        await service.provision(provision())
    assert len(repository._accounts) == 1


async def test_email_failure_preserves_pending_account_and_safe_resend(authority) -> None:
    service, repository, adapter = authority
    await service.import_employee(employee())

    async def failed_delivery(message: EmailMessage) -> DeliveryResult:
        return DeliveryResult(success=False, recipient=message.to_email, error="SMTP unavailable")

    adapter.send = failed_delivery
    account = await service.provision(provision())
    assert account["email_delivered"] is False
    assert account["activation_state"] == "PENDING"
    previous_hash = next(iter(repository._activation_challenges))
    resent = await service.resend_activation(
        account["actor_id"], tenant_id="tenant_test", organization_id="org_test"
    )
    assert resent["email_delivered"] is False
    assert previous_hash not in repository._activation_challenges
    assert len(repository._activation_challenges) == 1
    assert len(repository._accounts) == 2
    assert "token" not in str(account)


async def test_reset_expiry_replacement_and_session_revocation(authority) -> None:
    service, repository, adapter = authority
    await service.import_employee(employee())
    await service.provision(provision())
    activation_token = (
        adapter.sent_messages[-1].text_content.split("/aktivasi?token=")[1].split()[0]
    )
    await service.activate(activation_token, "EmployeePass!123", "EmployeePass!123")
    login = await service.login("employee@example.test", "EmployeePass!123")
    await service.request_password_reset("employee@example.test")
    first_token = (
        adapter.sent_messages[-1].text_content.split("/atur-ulang-sandi?token=")[1].split()[0]
    )
    await service.request_password_reset("employee@example.test")
    second_token = (
        adapter.sent_messages[-1].text_content.split("/atur-ulang-sandi?token=")[1].split()[0]
    )
    with pytest.raises(PlatformError):
        await service.confirm_password_reset(first_token, "NewPassword!123", "NewPassword!123")
    second_hash = hashlib.sha256(second_token.encode()).hexdigest()
    actor_id, _ = repository._password_reset_challenges[second_hash]
    repository._password_reset_challenges[second_hash] = (
        actor_id,
        datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(PlatformError):
        await service.confirm_password_reset(second_token, "NewPassword!123", "NewPassword!123")
    await service.request_password_reset("employee@example.test")
    token = adapter.sent_messages[-1].text_content.split("/atur-ulang-sandi?token=")[1].split()[0]
    assert token not in repository._password_reset_challenges
    await service.confirm_password_reset(token, "NewPassword!123", "NewPassword!123")
    with pytest.raises(PlatformError):
        await service.whoami(login["access_token"])
    with pytest.raises(PlatformError):
        await service.confirm_password_reset(token, "NewPassword!123", "NewPassword!123")
    assert (await service.login("employee@example.test", "NewPassword!123"))["access_token"]
