"""Dedicated canonical schema adapters; resource policy lives in the owner service."""

from fastapi import Request
from fastapi.responses import JSONResponse

from alos.api.public.record_routes import CanonicalRecordRequest, register_record_routes, response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.hr.records import SPECS

MODELS: dict[tuple[str, str], type[CanonicalRecordRequest]] = {}


class HrEmployeeCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmployeeCreateRequest"
    )


MODELS[("employees", "create")] = HrEmployeeCreateRequest


class HrEmployeeUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmployeeUpdateRequest"
    )


MODELS[("employees", "update")] = HrEmployeeUpdateRequest


class HrEmployeeTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmployeeTransitionRequest"
    )


MODELS[("employees", "transition")] = HrEmployeeTransitionRequest


class HrAttendanceCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrAttendanceCreateRequest"
    )


MODELS[("attendances", "create")] = HrAttendanceCreateRequest


class HrLeaveRequestCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrLeaveRequestCreateRequest"
    )


MODELS[("leave_requests", "create")] = HrLeaveRequestCreateRequest


class HrLeaveRequestUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrLeaveRequestUpdateRequest"
    )


MODELS[("leave_requests", "update")] = HrLeaveRequestUpdateRequest


class HrLeaveRequestTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrLeaveRequestTransitionRequest"


MODELS[("leave_requests", "transition")] = HrLeaveRequestTransitionRequest


class HrRecruitmentCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrRecruitmentCreateRequest"
    )


MODELS[("recruitments", "create")] = HrRecruitmentCreateRequest


class HrRecruitmentUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrRecruitmentUpdateRequest"
    )


MODELS[("recruitments", "update")] = HrRecruitmentUpdateRequest


class HrRecruitmentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrRecruitmentTransitionRequest"


MODELS[("recruitments", "transition")] = HrRecruitmentTransitionRequest


class HrCandidateCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrCandidateCreateRequest"
    )


MODELS[("candidates", "create")] = HrCandidateCreateRequest


class HrCandidateUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrCandidateUpdateRequest"
    )


MODELS[("candidates", "update")] = HrCandidateUpdateRequest


class HrCandidateTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrCandidateTransitionRequest"


MODELS[("candidates", "transition")] = HrCandidateTransitionRequest


class HrInterviewCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrInterviewCreateRequest"
    )


MODELS[("interviews", "create")] = HrInterviewCreateRequest


class HrInterviewUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrInterviewUpdateRequest"
    )


MODELS[("interviews", "update")] = HrInterviewUpdateRequest


class HrInterviewTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrInterviewTransitionRequest"


MODELS[("interviews", "transition")] = HrInterviewTransitionRequest


class HrOnboardingCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrOnboardingCreateRequest"
    )


MODELS[("onboardings", "create")] = HrOnboardingCreateRequest


class HrOnboardingUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrOnboardingUpdateRequest"
    )


MODELS[("onboardings", "update")] = HrOnboardingUpdateRequest


class HrOnboardingTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrOnboardingTransitionRequest"


MODELS[("onboardings", "transition")] = HrOnboardingTransitionRequest


class HrPerformanceReviewCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPerformanceReviewCreateRequest"


MODELS[("performance_reviews", "create")] = HrPerformanceReviewCreateRequest


class HrPerformanceReviewUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPerformanceReviewUpdateRequest"


MODELS[("performance_reviews", "update")] = HrPerformanceReviewUpdateRequest


class HrPerformanceReviewTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPerformanceReviewTransitionRequest"


MODELS[("performance_reviews", "transition")] = HrPerformanceReviewTransitionRequest


class HrTrainingCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrTrainingCreateRequest"
    )


MODELS[("trainings", "create")] = HrTrainingCreateRequest


class HrTrainingUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrTrainingUpdateRequest"
    )


MODELS[("trainings", "update")] = HrTrainingUpdateRequest


class HrTrainingTransitionRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrTrainingTransitionRequest"
    )


MODELS[("trainings", "transition")] = HrTrainingTransitionRequest


class HrTrainingEnrollmentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrTrainingEnrollmentCreateRequest"


MODELS[("training_enrollments", "create")] = HrTrainingEnrollmentCreateRequest


class HrTrainingEnrollmentTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrTrainingEnrollmentTransitionRequest"


MODELS[("training_enrollments", "transition")] = HrTrainingEnrollmentTransitionRequest


class HrSuccessionCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrSuccessionCreateRequest"
    )


MODELS[("successions", "create")] = HrSuccessionCreateRequest


class HrSuccessionUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrSuccessionUpdateRequest"
    )


MODELS[("successions", "update")] = HrSuccessionUpdateRequest


class HrSuccessionTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrSuccessionTransitionRequest"


MODELS[("successions", "transition")] = HrSuccessionTransitionRequest


class HrSuccessionCandidateCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrSuccessionCandidateCreateRequest"


MODELS[("succession_candidates", "create")] = HrSuccessionCandidateCreateRequest


class HrSuccessionCandidateUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrSuccessionCandidateUpdateRequest"


MODELS[("succession_candidates", "update")] = HrSuccessionCandidateUpdateRequest


class HrGrievanceCreateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrGrievanceCreateRequest"
    )


MODELS[("grievances", "create")] = HrGrievanceCreateRequest


class HrGrievanceUpdateRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrGrievanceUpdateRequest"
    )


MODELS[("grievances", "update")] = HrGrievanceUpdateRequest


class HrGrievanceTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrGrievanceTransitionRequest"


MODELS[("grievances", "transition")] = HrGrievanceTransitionRequest


class HrEmploymentContractCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmploymentContractCreateRequest"


MODELS[("employment_contracts", "create")] = HrEmploymentContractCreateRequest


class HrEmploymentContractUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmploymentContractUpdateRequest"


MODELS[("employment_contracts", "update")] = HrEmploymentContractUpdateRequest


class HrEmploymentContractTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmploymentContractTransitionRequest"


MODELS[("employment_contracts", "transition")] = HrEmploymentContractTransitionRequest


class HrPersonnelFileCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPersonnelFileCreateRequest"


MODELS[("personnel_files", "create")] = HrPersonnelFileCreateRequest


class HrPersonnelFileUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPersonnelFileUpdateRequest"


MODELS[("personnel_files", "update")] = HrPersonnelFileUpdateRequest


class HrPersonnelFileTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrPersonnelFileTransitionRequest"


MODELS[("personnel_files", "transition")] = HrPersonnelFileTransitionRequest


class HrFacilityRequestCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrFacilityRequestCreateRequest"


MODELS[("facility_requests", "create")] = HrFacilityRequestCreateRequest


class HrFacilityRequestUpdateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrFacilityRequestUpdateRequest"


MODELS[("facility_requests", "update")] = HrFacilityRequestUpdateRequest


class HrFacilityRequestTransitionRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrFacilityRequestTransitionRequest"


MODELS[("facility_requests", "transition")] = HrFacilityRequestTransitionRequest


class HrInventoryItemCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrInventoryItemCreateRequest"


MODELS[("inventory_items", "create")] = HrInventoryItemCreateRequest


class HrAssetHandoverCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrAssetHandoverCreateRequest"


MODELS[("asset_handovers", "create")] = HrAssetHandoverCreateRequest


class HrMaintenanceRecordCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrMaintenanceRecordCreateRequest"


MODELS[("maintenance_records", "create")] = HrMaintenanceRecordCreateRequest


class HrServiceAssessmentCreateRequest(CanonicalRecordRequest):
    schema_uri = "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrServiceAssessmentCreateRequest"


MODELS[("service_assessments", "create")] = HrServiceAssessmentCreateRequest


router = register_record_routes("hr", SPECS, MODELS)


class HrCandidateHireRequest(CanonicalRecordRequest):
    schema_uri = (
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrCandidateHireRequest"
    )


@router.post("/candidates/{candidate_id}/hire")
async def hire_candidate(
    candidate_id: str,
    payload: HrCandidateHireRequest,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> JSONResponse:
    employee = await request.app.state.hr_service.hire(
        principal, candidate_id, payload.validated(contracts)
    )
    return response(
        contracts,
        "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/HrEmployeeProjection",
        employee,
    )
