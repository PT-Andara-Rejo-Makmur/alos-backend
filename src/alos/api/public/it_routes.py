"""Dedicated canonical schema adapters; resource policy lives in the owner service."""

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes
from alos.domains.it.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class ItSystemCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSystemCreateRequest"
    )


MODELS[("systems", "create")] = ItSystemCreateRequest


class ItSystemUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSystemUpdateRequest"
    )


MODELS[("systems", "update")] = ItSystemUpdateRequest


class ItSystemTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSystemTransitionRequest"
    )


MODELS[("systems", "transition")] = ItSystemTransitionRequest


class ItIntegrationCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIntegrationCreateRequest"
    )


MODELS[("integrations", "create")] = ItIntegrationCreateRequest


class ItIntegrationUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIntegrationUpdateRequest"
    )


MODELS[("integrations", "update")] = ItIntegrationUpdateRequest


class ItIntegrationTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIntegrationTransitionRequest"


MODELS[("integrations", "transition")] = ItIntegrationTransitionRequest


class ItDatabaseCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDatabaseCreateRequest"
    )


MODELS[("databases", "create")] = ItDatabaseCreateRequest


class ItDatabaseUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDatabaseUpdateRequest"
    )


MODELS[("databases", "update")] = ItDatabaseUpdateRequest


class ItDatabaseTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDatabaseTransitionRequest"
    )


MODELS[("databases", "transition")] = ItDatabaseTransitionRequest


class ItEnvironmentCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItEnvironmentCreateRequest"
    )


MODELS[("environments", "create")] = ItEnvironmentCreateRequest


class ItEnvironmentUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItEnvironmentUpdateRequest"
    )


MODELS[("environments", "update")] = ItEnvironmentUpdateRequest


class ItEnvironmentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItEnvironmentTransitionRequest"


MODELS[("environments", "transition")] = ItEnvironmentTransitionRequest


class ItRepositoryCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItRepositoryCreateRequest"
    )


MODELS[("repositories", "create")] = ItRepositoryCreateRequest


class ItRepositoryUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItRepositoryUpdateRequest"
    )


MODELS[("repositories", "update")] = ItRepositoryUpdateRequest


class ItRepositoryTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItRepositoryTransitionRequest"


MODELS[("repositories", "transition")] = ItRepositoryTransitionRequest


class ItCicdPipelineCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItCicdPipelineCreateRequest"
    )


MODELS[("cicd_pipelines", "create")] = ItCicdPipelineCreateRequest


class ItCicdPipelineUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItCicdPipelineUpdateRequest"
    )


MODELS[("cicd_pipelines", "update")] = ItCicdPipelineUpdateRequest


class ItCicdPipelineTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItCicdPipelineTransitionRequest"


MODELS[("cicd_pipelines", "transition")] = ItCicdPipelineTransitionRequest


class ItCiRunCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItCiRunCreateRequest"
    )


MODELS[("ci_runs", "create")] = ItCiRunCreateRequest


class ItReleaseCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItReleaseCreateRequest"
    )


MODELS[("releases", "create")] = ItReleaseCreateRequest


class ItReleaseUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItReleaseUpdateRequest"
    )


MODELS[("releases", "update")] = ItReleaseUpdateRequest


class ItReleaseTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItReleaseTransitionRequest"
    )


MODELS[("releases", "transition")] = ItReleaseTransitionRequest


class ItTechnicalDebtCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItTechnicalDebtCreateRequest"


MODELS[("technical_debts", "create")] = ItTechnicalDebtCreateRequest


class ItTechnicalDebtUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItTechnicalDebtUpdateRequest"


MODELS[("technical_debts", "update")] = ItTechnicalDebtUpdateRequest


class ItTechnicalDebtTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItTechnicalDebtTransitionRequest"


MODELS[("technical_debts", "transition")] = ItTechnicalDebtTransitionRequest


class ItServiceMonitorCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItServiceMonitorCreateRequest"


MODELS[("service_monitors", "create")] = ItServiceMonitorCreateRequest


class ItIncidentCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIncidentCreateRequest"
    )


MODELS[("incidents", "create")] = ItIncidentCreateRequest


class ItIncidentUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIncidentUpdateRequest"
    )


MODELS[("incidents", "update")] = ItIncidentUpdateRequest


class ItIncidentTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItIncidentTransitionRequest"
    )


MODELS[("incidents", "transition")] = ItIncidentTransitionRequest


class ItSecurityFindingCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSecurityFindingCreateRequest"


MODELS[("security_findings", "create")] = ItSecurityFindingCreateRequest


class ItSecurityFindingUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSecurityFindingUpdateRequest"


MODELS[("security_findings", "update")] = ItSecurityFindingUpdateRequest


class ItSecurityFindingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItSecurityFindingTransitionRequest"


MODELS[("security_findings", "transition")] = ItSecurityFindingTransitionRequest


class ItBackupPolicyCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItBackupPolicyCreateRequest"
    )


MODELS[("backup_policies", "create")] = ItBackupPolicyCreateRequest


class ItBackupPolicyUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItBackupPolicyUpdateRequest"
    )


MODELS[("backup_policies", "update")] = ItBackupPolicyUpdateRequest


class ItBackupPolicyTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItBackupPolicyTransitionRequest"


MODELS[("backup_policies", "transition")] = ItBackupPolicyTransitionRequest


class ItBackupRunCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItBackupRunCreateRequest"
    )


MODELS[("backup_runs", "create")] = ItBackupRunCreateRequest


class ItRestoreTestCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItRestoreTestCreateRequest"
    )


MODELS[("restore_tests", "create")] = ItRestoreTestCreateRequest


class ItDrPlanCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDrPlanCreateRequest"
    )


MODELS[("dr_plans", "create")] = ItDrPlanCreateRequest


class ItDrPlanUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDrPlanUpdateRequest"
    )


MODELS[("dr_plans", "update")] = ItDrPlanUpdateRequest


class ItDrPlanTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/ItDrPlanTransitionRequest"
    )


MODELS[("dr_plans", "transition")] = ItDrPlanTransitionRequest

router = register_record_routes("it", SPECS, MODELS)
