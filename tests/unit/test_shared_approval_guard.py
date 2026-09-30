"""Generic domain writes cannot decide or erase Shared Work approvals."""

from unittest.mock import Mock

import pytest

from alos.domains.crud import DomainCrudService
from alos.security.errors import PlatformError


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_generic_approval_mutation_is_rejected_before_database_access(
    operation: str,
) -> None:
    session_factory = Mock()
    service = DomainCrudService(session_factory)
    resource = service.resource("shared", "work_approvals")
    principal = Mock()

    with pytest.raises(PlatformError) as rejected:
        if operation == "create":
            await service.create_record(
                resource,
                principal,
                {"subject_type": "TASK", "subject_id": "task_123", "status": "APPROVED"},
            )
        elif operation == "update":
            await service.update_record(
                resource,
                principal,
                "approval_123",
                {"decision": "APPROVED", "decided_at": "2026-09-30T00:00:00Z"},
            )
        else:
            await service.delete_record(resource, principal, "approval_123")

    assert rejected.value.code == "APPROVAL_LIFECYCLE_REQUIRES_DEDICATED_API"
    assert rejected.value.status_code == 409
    session_factory.assert_not_called()
