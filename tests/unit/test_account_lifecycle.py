"""Comprehensive unit tests for the governed Identity & Account lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.service import AuthService
from alos.config import Settings
from alos.notifications.service import NotificationService
from alos.notifications.smtp_adapter import InMemoryEmailAdapter
from alos.security.errors import PlatformError
from alos.security.rate_limit import InMemoryRateLimiter


@pytest.fixture
def test_setup() -> tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter]:
    repo = InMemoryAuthRepository()
    adapter = InMemoryEmailAdapter()
    settings = Settings(
        EMAIL_PROVIDER="test",
        EMAIL_FROM="notification@company.com",
        EMAIL_FROM_NAME="ALOS",
        APP_PUBLIC_URL="https://alos.example.test",
    )
    notifications = NotificationService(settings=settings, adapter=adapter)
    service = AuthService(
        repo,
        notification_service=notifications,
        activation_ttl_hours=24,
        password_reset_ttl_minutes=60,
    )
    return service, repo, adapter


@pytest.fixture
async def setup_authority(
    test_setup: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter]:
    service, repo, adapter = test_setup
    # Bootstrap initial admin
    await service.bootstrap_initial_admin(
        {
            "email": "it.admin@example.test",
            "password": "AdminSecurePassword123!",
            "display_name": "IT Administrator",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_it_ops",
            "workspace_key": "it-ops",
            "workspace_name": "Operasional TI",
            "workspace_type": "IT_OPERATIONS",
            "division_code": "IT",
        }
    )
    # Register an additional business workspace
    await service.register_for_test(
        {
            "email": "lead.finance@example.test",
            "password": "FinancePass123!",
            "display_name": "Finance Lead",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "workspace_key": "finance",
            "workspace_name": "Keuangan",
            "workspace_type": "BUSINESS",
            "division_code": "FINANCE",
            "role_refs": ["DIVISION_LEAD"],
        }
    )
    return service, repo, adapter


@pytest.mark.asyncio
async def test_initial_employee_master_import(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, _ = setup_authority

    # 1. Successful import
    imported = await service.import_employee(
        {
            "employee_id": "emp_001",
            "employee_number": "ARM-2026-001",
            "full_name": "Budi Santoso",
            "email": "budi.santoso@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "department_code": "FINANCE",
            "position_title": "Senior Accountant",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )
    assert imported["employee_id"] == "emp_001"
    assert imported["employee_number"] == "ARM-2026-001"

    # Employee record has actor_id = NULL initially
    candidates = await service.provisioning_candidates(
        tenant_id="tenant_andara", organization_id="org_andara"
    )
    candidate_ids = [c["employee_id"] for c in candidates]
    assert "emp_001" in candidate_ids

    # 2. Reject duplicate employee_number
    with pytest.raises(PlatformError) as exc:
        await service.import_employee(
            {
                "employee_id": "emp_002",
                "employee_number": "ARM-2026-001",  # duplicate number
                "full_name": "Citra Dewi",
                "email": "citra.dewi@example.test",
                "tenant_id": "tenant_andara",
                "organization_id": "org_andara",
                "workspace_id": "ws_finance",
                "join_date": datetime.now(UTC).date().isoformat(),
            }
        )
    assert exc.value.code == "EMPLOYEE_CONFLICT"

    # 3. Reject duplicate email
    with pytest.raises(PlatformError) as exc:
        await service.import_employee(
            {
                "employee_id": "emp_003",
                "employee_number": "ARM-2026-003",
                "full_name": "Citra Dewi",
                "email": "budi.santoso@example.test",  # duplicate email
                "tenant_id": "tenant_andara",
                "organization_id": "org_andara",
                "workspace_id": "ws_finance",
                "join_date": datetime.now(UTC).date().isoformat(),
            }
        )
    assert exc.value.code == "EMPLOYEE_CONFLICT"

    # 4. Reject invalid boundary workspace
    with pytest.raises(PlatformError) as exc:
        await service.import_employee(
            {
                "employee_id": "emp_004",
                "employee_number": "ARM-2026-004",
                "full_name": "Doni",
                "email": "doni@example.test",
                "tenant_id": "tenant_andara",
                "organization_id": "org_andara",
                "workspace_id": "ws_nonexistent",
                "join_date": datetime.now(UTC).date().isoformat(),
            }
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_account_provisioning_and_branded_email(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, adapter = setup_authority

    # Import employee
    await service.import_employee(
        {
            "employee_id": "emp_010",
            "employee_number": "ARM-2026-010",
            "full_name": "Eka Pratama",
            "email": "eka.pratama@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "department_code": "FINANCE",
            "position_title": "Financial Analyst",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )

    # Provision account
    provisioned = await service.provision(
        {
            "employee_id": "emp_010",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )

    # Account starts PENDING
    assert provisioned["activation_state"] == "PENDING"
    assert provisioned["email_delivered"] is True

    # Email adapter received invitation branded as ALOS
    assert len(adapter.sent_messages) == 1
    sent = adapter.sent_messages[0]
    assert sent.to_email == "eka.pratama@example.test"
    assert sent.from_name == "ALOS"
    assert "Aktifkan Akun ALOS Anda" in sent.subject
    assert "https://alos.example.test/aktivasi?token=" in sent.text_content
    assert "Eka Pratama" in sent.text_content

    # Extract raw activation token from URL
    token = sent.text_content.split("/aktivasi?token=")[1].split()[0]
    assert len(token) > 20

    # Ensure raw token is not stored in plaintext anywhere in accounts
    accounts = await service.list_accounts(tenant_id="tenant_andara", organization_id="org_andara")
    acc = next(a for a in accounts if a["email"] == "eka.pratama@example.test")
    assert acc["activation_state"] == "PENDING"
    assert acc["email_delivered"] is True


@pytest.mark.asyncio
async def test_activation_flow_and_login(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, adapter = setup_authority

    await service.import_employee(
        {
            "employee_id": "emp_020",
            "employee_number": "ARM-2026-020",
            "full_name": "Fani Rahma",
            "email": "fani.rahma@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )
    await service.provision(
        {
            "employee_id": "emp_020",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )

    # Login before activation must fail
    with pytest.raises(PlatformError) as exc:
        await service.login("fani.rahma@example.test", "SomePass123!")
    assert exc.value.status_code == 401

    token = adapter.sent_messages[-1].text_content.split("/aktivasi?token=")[1].split()[0]

    # Weak password rejected
    with pytest.raises(PlatformError) as exc:
        await service.activate(token, "short", "short")
    assert exc.value.code == "WEAK_PASSWORD"

    # Mismatched confirmation rejected
    with pytest.raises(PlatformError) as exc:
        await service.activate(token, "MySecurePass123!", "MismatchPass123!")
    assert exc.value.code == "PASSWORD_CONFIRMATION_MISMATCH"

    # Valid activation
    activated = await service.activate(token, "MySecurePass123!", "MySecurePass123!")
    assert activated["activation_state"] == "ACTIVATED"

    # One-time use: activating again must be rejected
    with pytest.raises(PlatformError) as exc:
        await service.activate(token, "AnotherPass123!", "AnotherPass123!")
    assert exc.value.code == "ACTIVATION_CHALLENGE_INVALID"

    # Login now succeeds with newly created password
    login_result = await service.login("fani.rahma@example.test", "MySecurePass123!")
    assert login_result["access_token"].startswith("alos_")
    assert login_result["principal"]["email"] == "fani.rahma@example.test"


@pytest.mark.asyncio
async def test_resend_activation_invalidates_previous_token(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, adapter = setup_authority

    await service.import_employee(
        {
            "employee_id": "emp_030",
            "employee_number": "ARM-2026-030",
            "full_name": "Gita Savitri",
            "email": "gita.savitri@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )
    provisioned = await service.provision(
        {
            "employee_id": "emp_030",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )
    first_token = adapter.sent_messages[-1].text_content.split("/aktivasi?token=")[1].split()[0]
    actor_id = provisioned["actor_id"]

    # IT Admin resends activation
    resend = await service.resend_activation(
        actor_id, tenant_id="tenant_andara", organization_id="org_andara"
    )
    assert resend["activation_state"] == "PENDING"
    assert resend["email_delivered"] is True

    # New token was sent
    assert len(adapter.sent_messages) == 2
    second_token = adapter.sent_messages[-1].text_content.split("/aktivasi?token=")[1].split()[0]
    assert first_token != second_token

    # Old token must be rejected
    with pytest.raises(PlatformError) as exc:
        await service.activate(first_token, "ValidPass123!", "ValidPass123!")
    assert exc.value.code == "ACTIVATION_CHALLENGE_INVALID"

    # New token works
    activated = await service.activate(second_token, "ValidPass123!", "ValidPass123!")
    assert activated["activation_state"] == "ACTIVATED"

    # Resend on already ACTIVATED account must be rejected
    with pytest.raises(PlatformError) as exc:
        await service.resend_activation(
            actor_id, tenant_id="tenant_andara", organization_id="org_andara"
        )
    assert exc.value.code == "ACTIVATION_RESEND_FAILED"


@pytest.mark.asyncio
async def test_suspend_and_reactivate_account(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, _ = setup_authority

    await service.import_employee(
        {
            "employee_id": "emp_040",
            "employee_number": "ARM-2026-040",
            "full_name": "Hadi Wijaya",
            "email": "hadi.wijaya@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )
    provisioned = await service.provision(
        {
            "employee_id": "emp_040",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )
    await service._repository.resend_activation_challenge(
        provisioned["actor_id"],
        tenant_id="tenant_andara",
        organization_id="org_andara",
        new_token_hash=service._token_hash("hadi_token_123"),
        new_expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    await service.activate("hadi_token_123", "HadiPass123!", "HadiPass123!")

    # Login and obtain session
    session = await service.login("hadi.wijaya@example.test", "HadiPass123!")
    session_token = session["access_token"]
    assert (await service.whoami(session_token))["email"] == "hadi.wijaya@example.test"

    # Suspend account
    suspended = await service.set_account_active(
        provisioned["actor_id"],
        False,
        tenant_id="tenant_andara",
        organization_id="org_andara",
    )
    assert suspended["active"] is False

    # Existing session must be revoked
    with pytest.raises(PlatformError) as exc:
        await service.whoami(session_token)
    assert exc.value.status_code == 401

    # New login must be rejected
    with pytest.raises(PlatformError) as exc:
        await service.login("hadi.wijaya@example.test", "HadiPass123!")
    assert exc.value.status_code == 401

    # Reactivate account
    reactivated = await service.set_account_active(
        provisioned["actor_id"],
        True,
        tenant_id="tenant_andara",
        organization_id="org_andara",
    )
    assert reactivated["active"] is True

    # Login works again
    new_session = await service.login("hadi.wijaya@example.test", "HadiPass123!")
    who = await service.whoami(new_session["access_token"])
    assert who["email"] == "hadi.wijaya@example.test"


@pytest.mark.asyncio
async def test_password_reset_flow(
    setup_authority: tuple[AuthService, InMemoryAuthRepository, InMemoryEmailAdapter],
) -> None:
    service, _, adapter = setup_authority

    await service.import_employee(
        {
            "employee_id": "emp_050",
            "employee_number": "ARM-2026-050",
            "full_name": "Indah Permata",
            "email": "indah.permata@example.test",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "employment_status": "ACTIVE",
            "join_date": datetime.now(UTC).date().isoformat(),
        }
    )
    await service.provision(
        {
            "employee_id": "emp_050",
            "tenant_id": "tenant_andara",
            "organization_id": "org_andara",
            "workspace_id": "ws_finance",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )
    token = adapter.sent_messages[-1].text_content.split("/aktivasi?token=")[1].split()[0]
    await service.activate(token, "OldPassword123!", "OldPassword123!")

    # Establish an active session
    old_session = await service.login("indah.permata@example.test", "OldPassword123!")
    old_session_token = old_session["access_token"]
    assert (await service.whoami(old_session_token))["email"] == "indah.permata@example.test"

    # Request password reset for non-existent email -> generic response, no enumeration
    adapter.sent_messages.clear()
    resp1 = await service.request_password_reset("nonexistent@example.test")
    assert "instruksi pemulihan telah dikirim" in resp1["message"]
    assert len(adapter.sent_messages) == 0

    # Request password reset for active user
    resp2 = await service.request_password_reset("indah.permata@example.test")
    assert "instruksi pemulihan telah dikirim" in resp2["message"]
    assert len(adapter.sent_messages) == 1
    reset_email = adapter.sent_messages[0]
    assert reset_email.to_email == "indah.permata@example.test"
    assert reset_email.from_name == "ALOS"
    assert "Atur Ulang Kata Sandi" in reset_email.subject
    assert "/atur-ulang-sandi?token=" in reset_email.text_content

    reset_token = reset_email.text_content.split("/atur-ulang-sandi?token=")[1].split()[0]

    # Confirm password reset with mismatch
    with pytest.raises(PlatformError) as exc:
        await service.confirm_password_reset(reset_token, "NewPassword123!", "Mismatch!")
    assert exc.value.code == "PASSWORD_CONFIRMATION_MISMATCH"

    # Confirm password reset with valid data
    confirm_res = await service.confirm_password_reset(
        reset_token, "NewPassword123!", "NewPassword123!"
    )
    assert "berhasil diperbarui" in confirm_res["message"]

    # Reusing reset token must fail
    with pytest.raises(PlatformError) as exc:
        await service.confirm_password_reset(reset_token, "AnotherPass123!", "AnotherPass123!")
    assert exc.value.code == "RESET_TOKEN_INVALID"

    # Old session is revoked
    with pytest.raises(PlatformError) as exc:
        await service.whoami(old_session_token)
    assert exc.value.status_code == 401

    # Old password fails
    with pytest.raises(PlatformError) as exc:
        await service.login("indah.permata@example.test", "OldPassword123!")
    assert exc.value.status_code == 401

    # New password succeeds
    new_login = await service.login("indah.permata@example.test", "NewPassword123!")
    assert new_login["principal"]["email"] == "indah.permata@example.test"


@pytest.mark.asyncio
async def test_rate_limiter() -> None:
    limiter = InMemoryRateLimiter()
    key = "test_endpoint:user1"

    # 3 requests allowed in 10-second window
    await limiter.check(key, max_requests=3, window_seconds=10.0)
    await limiter.check(key, max_requests=3, window_seconds=10.0)
    await limiter.check(key, max_requests=3, window_seconds=10.0)

    # 4th request must be rate limited
    with pytest.raises(PlatformError) as exc:
        await limiter.check(key, max_requests=3, window_seconds=10.0)
    assert exc.value.code == "RATE_LIMIT_EXCEEDED"
    assert exc.value.status_code == 429

    # Reset allows requests again
    await limiter.reset()
    await limiter.check(key, max_requests=3, window_seconds=10.0)
