# Legal, HR and IT canonical operations

These three owners use existing migration-owned tables through RecordRepository. Dedicated commands validate canonical contracts in both the API adapter and owner service. Record state and audit append share one PostgreSQL transaction.

## Persistence and API

| Owner | Existing migration | Namespace | Resources |
| --- | --- | --- | --- |
| Legal | 0016_legal.py | /api/v1/legal | 11 |
| HR | 0015_hr.py | /api/v1/hr | 15 |
| IT | 0019_it_operations.py | /api/v1/it | 16 |

No migration is added or rewritten. GET overview, list and detail are available for every resource; POST creates a scoped record. PATCH exists only when update fields exist. Transition exists only when the owner defines an internal lifecycle. No dedicated DELETE exists. Generic Legal/HR/IT mutations return CANONICAL_DOMAIN_MUTATION_REQUIRED.

## Internal lifecycle

These states describe internal records. They do not establish corporate legal, employment or production decision authority. Projection allowed_transitions is computed by the backend using state, role and domain write permission. Unknown persisted states retain their exact value, expose no transitions and reject mutations.

| Owner resource | Initial state | Available edges | PATCH |
| --- | --- | --- | --- |
| legal.permits | RECORDED | RECORDED → ARCHIVED | Yes |
| legal.contracts | DRAFT | DRAFT → IN_REVIEW; IN_REVIEW → DRAFT | Yes |
| legal.land_documents | RECORDED | RECORDED → ARCHIVED | Yes |
| legal.due_diligences | OPEN | OPEN → IN_REVIEW; IN_REVIEW → OPEN | Yes |
| legal.due_diligence_items | OPEN | OPEN → IN_PROGRESS; IN_PROGRESS → COMPLETED | Yes |
| legal.cases | OPEN | OPEN → IN_REVIEW; IN_REVIEW → OPEN | Yes |
| legal.claim_reviews | OPEN | OPEN → IN_REVIEW; IN_REVIEW → REVIEWED | Yes |
| legal.expiries | OPEN | OPEN → ACKNOWLEDGED | Yes |
| legal.privacy_requests | OPEN | OPEN → IN_PROGRESS | Yes |
| legal.risks | OPEN | OPEN → MITIGATING; MITIGATING → REVIEWED | Yes |
| legal.controls | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| hr.employees | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| hr.attendances | Explicit recorded input | Immutable recorded evidence | No |
| hr.leave_requests | PENDING | PENDING → WITHDRAWN | Yes |
| hr.recruitments | OPEN | OPEN → ON_HOLD; OPEN → CLOSED; ON_HOLD → OPEN; ON_HOLD → CLOSED | Yes |
| hr.candidates | APPLIED | APPLIED → SCREENING; SCREENING → INTERVIEW | Yes |
| hr.interviews | SCHEDULED | SCHEDULED → COMPLETED; SCHEDULED → CANCELLED | Yes |
| hr.onboardings | OPEN | OPEN → IN_PROGRESS; IN_PROGRESS → COMPLETED | Yes |
| hr.performance_reviews | DRAFT | DRAFT → IN_REVIEW; IN_REVIEW → DRAFT; IN_REVIEW → REVIEWED | Yes |
| hr.trainings | PLANNED | PLANNED → IN_PROGRESS; PLANNED → CANCELLED; IN_PROGRESS → COMPLETED | Yes |
| hr.training_enrollments | ENROLLED | ENROLLED → COMPLETED; ENROLLED → CANCELLED | No |
| hr.successions | OPEN | OPEN → IN_REVIEW; IN_REVIEW → OPEN | Yes |
| hr.succession_candidates | Explicit recorded input | No lifecycle; explicit recorded readiness | Yes |
| hr.grievances | OPEN | OPEN → IN_REVIEW; IN_REVIEW → OPEN | Yes |
| hr.employment_contracts | DRAFT | DRAFT → IN_REVIEW; IN_REVIEW → DRAFT | Yes |
| hr.personnel_files | RECORDED | RECORDED → ARCHIVED | Yes |
| it.systems | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.integrations | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.databases | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.environments | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.repositories | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.cicd_pipelines | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.ci_runs | Explicit recorded input | Immutable recorded evidence | No |
| it.releases | PLANNED | PLANNED → IN_REVIEW; PLANNED → CANCELLED; IN_REVIEW → PLANNED; IN_REVIEW → CANCELLED | Yes |
| it.technical_debts | OPEN | OPEN → IN_PROGRESS; IN_PROGRESS → RESOLVED; RESOLVED → CLOSED | Yes |
| it.service_monitors | Explicit recorded input | Immutable recorded evidence | No |
| it.incidents | OPEN | OPEN → INVESTIGATING; INVESTIGATING → RESOLVED; RESOLVED → CLOSED | Yes |
| it.security_findings | OPEN | OPEN → IN_REVIEW; IN_REVIEW → REMEDIATING; REMEDIATING → RESOLVED; RESOLVED → CLOSED | Yes |
| it.backup_policies | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |
| it.backup_runs | Explicit recorded input | Immutable recorded evidence | No |
| it.restore_tests | Explicit recorded input | Immutable recorded evidence | No |
| it.dr_plans | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE | Yes |

## Authority and evidence

DIVISION_MEMBER and DIVISION_LEAD require the relevant read/write permissions. Legal REVIEWED, HR REVIEWED/recruitment CLOSED/employee ACTIVE or INACTIVE, and IT CLOSED additionally require DIVISION_LEAD. EXECUTIVE cannot issue owner commands. IT_ADMIN can operate IT only with it.read/it.write in an active IT_OPERATIONS workspace and cannot write HR or Legal. Division defaults include strategy.read, without strategy.company.manage.

Legal subject references resolve only scoped Legal contracts, permits, land documents or cases. property_ref remains opaque metadata, with no fabricated Property FK or write port. Legal/HR document_id references use the existing Shared Work document visibility check in the mutation transaction.

HR employee records remain distinct from Identity actors and auth accounts. HR cannot set actor_id, create accounts, change memberships or revoke access. Inactivating an employee leaves account state unchanged. Active employees are required for attendance, leave, onboarding and training enrollment. Candidate/recruitment and other HR parent references require the same tenant, organization and workspace. Grievances remain scoped. Ratings and readiness are explicit input. Interview completion requires notes; review completion requires rating and summary.

IT system, repository, environment, pipeline and backup references use the same scoped owner tables. CI runs, backup runs, restore tests and monitor observations are immutable recorded evidence. Outcomes require explicit completion/evidence input; last_checked_at is required and cannot be a future timestamp. Loading a page never updates recorded health. Inventory commands have no external connector or execution port. Severity, criticality and priority are explicit input. Integrations reject credential fields; URL metadata excludes embedded credentials and query secrets.

Client payloads reject tenant/organization/workspace IDs, timestamps and authority actor metadata. Initial states, record IDs, created/updated timestamps and author metadata are server controlled. Attendance/CI/backup/monitor recorded_status is explicit evidence input, not a transition command. Claim amounts use exact bounded decimal strings.

## Unavailable material actions

Legal signature validation, externally verified permits, legal judgments and automatic regulatory compliance remain unavailable. Compensation/payroll/protected personal fields, live monitoring/GitHub execution, external connectors, provider-secret storage in IT inventory and unsupported asset/helpdesk entities remain unavailable. Internal GA facilities, configured recruitment/hiring/offboarding, Identity handoffs and recorded IT-release workflows are implemented as described in [business operations](business-operations.md); those records do not execute external services or grant legal validity. ARA has a governed runtime with real-model eval pending. Agent production release continues through existing Governance and human decisions.

## Web and Executive

Existing Legal pages connect contracts, permits, land documents, due diligence/items, cases/claim reviews, expiries/privacy requests and risks/controls. HR pages connect employees, recruitment/candidates/interviews, onboarding, attendance/leave, performance/training/enrollments/succession, grievances and documents. IT pages connect systems, environments/databases, recorded monitors/incidents, inventory integrations, security/backup/restore/DR and repositories/pipelines/CI/release/debt records. Existing Identity handles accounts and access; existing Shared Work handles projects/tasks/approvals/documents/reports/findings. Target & Kinerja uses canonical Strategy.

Unsupported capabilities show Belum Tersedia. Source failures show an error, unavailable sources never become zero counts, and only stored counts/statuses are shown. No derived compliance, turnover, attendance, security, uptime, MTTR or backup success metrics are introduced. Forms present every accepted field; detail projections retain every persisted column.

Executive now calls SALES (Sales plus Marketing), PROPERTY, FINANCE, LEGAL, HR and IT owner overview ports. Empty authoritative sources use CONNECTED_EMPTY; persisted data uses CONNECTED. Retrieval failures use ERROR with healthy sources retained and HTTP 200. Contract and authority/security failures propagate fail closed. DataScope.COMPANY is required for company-wide visibility; other principals retain workspace scope.

## Verification

tests/integration/test_legal_hr_it_domains.py uses migrated PostgreSQL and covers all 42 resources, scope/reference injection, immutable endpoints, generic mutation guards, authority/actor forgery, legacy state preservation, employee/account separation, IT_ADMIN boundaries, member/lead transitions, material actions and Executive visibility/failure semantics. Audit rollback tests insert an audit row then raise and prove both audit and business records are absent. Existing closed-domain regression tests remain in the full suite.

alos-infra/scripts/integration-smoke.py proves Web BFF → dedicated Backend → migrated PostgreSQL, material action denial and all six Executive owner sources. Existing infrastructure backup/restore proof remains separate from business API commands.
