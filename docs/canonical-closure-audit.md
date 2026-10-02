# Canonical closure audit

Audit date: 2026-10-02. All repositories were fetched; `development` and working trees were clean.

| Repository | Audited HEAD | Latest CI |
|---|---|---|
| alos-contracts | cbf821a31a1c245aa0cc05614fa324ae10798c0f | 36943352593 success |
| alos-backend | 6cb08a8f775bfa504401f59cf085e7627918ddc3 | 36944297413 success |
| alos-web | 366e15decb471d735561e9caa06c7f7bb0f93259 | 36943610845 success |
| alos-infra | 1a2617ed77a1c157521480e1a2754a9fe29acf51 | 36943937017 / 36943936990 success |
| genesis-ai (read only) | 1ffb055a82cc04d668a7582c0a38af03614fcb57 | 35974392590 success |

Migration chain is linear through `0028_password_reset_challenges`. Existing migrations will remain unchanged. Contract generation reads JSON Schema definitions, including Shared Work and business projections; generated files must be regenerated, never manually edited. Dedicated API adapters validate requests and projections. RecordRepository owns scoped persistence and atomic business audit. HR, Legal and IT each already have generated contracts, owner services, dedicated APIs, Web adapters, Executive ports and migrated PostgreSQL tests. Existing integration smoke proves these through the Web BFF and also proves deterministic GENESIS behavior.

## Gap decisions before implementation

| Feature | Existing Table | Canonical Entity / Contract | Service / API | Permission | Frontend | External Dependency | Decision | Status |
|---|---|---|---|---|---|---|---|---|
| Identity accounts/access | core actors/accounts/memberships/sessions | Identity generated contracts | Identity dedicated API | identity permissions | account administration | none | A: reuse, never duplicate in HR/IT | CONNECTED |
| Strategy planning/verification | strategy tables | Strategy contracts | StrategyService / strategy API | strategy permissions | existing Strategy adapters | none | A: retain | CONNECTED |
| Universal work objects | core projects/tasks/work_approvals/documents/reports/findings/links | Shared Work contracts | SharedWorkService / dedicated APIs | work object permissions | existing modules | none | A: retain universal authority | CONNECTED |
| Executive projections | owner read ports | Executive contracts | ExecutiveProjectionService / overview | read + COMPANY scope where applicable | Executive overview | none | A: retain owner source isolation | CONNECTED |
| Sales / Marketing | migration 0017 | sales/marketing contracts | owner services / dedicated APIs | domain.read/write | existing adapters | live advertising/CRM | A: retain internal records; C for live metrics | PARTIAL |
| Property | migration 0018 | Property contracts | PropertyService / property API | property.read/write | existing adapters | external property/asset feeds | A: retain | CONNECTED |
| Finance | migration 0014 | Finance contracts | FinanceService / finance API | finance.read/write | existing adapters | bank/DJP/payment providers | A: retain exact Decimal/period locking; C for live sources | PARTIAL |
| HR existing 15 resources | migration 0015 | HR contracts | HrService / hr API | hr.read/write | HR adapters | payroll/attendance hardware | A: retain; C/E for unsupported final actions | PARTIAL |
| Legal existing 11 resources | migration 0016 | Legal contracts | LegalService / legal API | legal.read/write | Legal adapters | signing/regulatory providers | A: retain; C for signing and verified validity | PARTIAL |
| IT existing 16 resources | migration 0019 | IT contracts | ItService / it API | it.read/write + IT workspace policy | IT adapters; Identity for access | GitHub/live monitoring | A: retain; C for live data | PARTIAL |
| Action-scoped material approvals | core.work_approvals + workspace links | Shared Work Approval extended with action, snapshot, consumption | owner subject read ports + Shared Work approval + explicit business transition | owner write; independent owner lead with approval decision permission | business request/status/execute + existing Approval UI | none | B: append minimal replay/snapshot fields, preserve PROJECT/TASK compatibility | GAP |
| Facility request | none | HR FacilityRequest | HrService / hr facility-requests | hr.read/write | GA module | none | B: internal explicit request lifecycle | GAP |
| Recorded inventory | none | HR InventoryItem | HrService / hr inventory-items | hr.read/write | GA module | optional future asset connector | B: immutable manual registry; C for external synchronization | GAP |
| Asset handover | none | HR AssetHandover | HrService / hr asset-handovers | hr.read/write | GA module | none | B: immutable explicit employee/item/date evidence; no silent access change | GAP |
| Maintenance history | none | HR MaintenanceRecord | HrService / hr maintenance-records | hr.read/write | GA module | external maintenance platform | B: immutable manually recorded event; C for live execution | GAP |
| Service readiness | none | HR ServiceAssessment | HrService / hr service-assessments | hr.read/write | GA module | none | B: immutable explicit human assessment, no inferred readiness | GAP |
| Legal Review | due_diligences/claim_reviews have different semantics | LegalReview | LegalService / legal-reviews | legal.read/write; lead review | existing Review module | none | B: minimal internal legal assessment; not business approval/signature/execution | GAP |
| Contract amendment/version reference | core.document_versions exists; no legal revision relation | LegalContractRevision | LegalService / contract-revisions | legal.read/write | contract module | external signing | B: immutable legal relation to actual immutable document version; no overwrite | GAP |
| HR hiring/rejection/termination, payroll, offboarding execution | no complete governed authority | no invented final states | fail closed | no new authority | unavailable explanation | business policy, Identity/Finance handoffs | E: retain blocked final decisions | UNAVAILABLE |
| Legal signature/execution/verified permit validity | absent provider/assessment authority | existing conservative records | fail closed | no inferred authority | source notice | signing/regulatory connector | C/E: defer | DEFERRED_TO_CONNECTOR |
| IT production release | existing Governance DecisionRef | existing governance contract | existing governed release API | existing IT/DIRECTOR authority | existing readiness | production policy | A/E: preserve authority, do not duplicate | PARTIAL |
| AI extraction/reasoning/provider runtime | existing deterministic test baseline only | no new production contract | unchanged | no AI decision authority | existing source states | GENESIS/ARA production | D: defer; genesis-ai unchanged | DEFERRED_TO_AI |

Material action scope: win opportunity, confirm booking, complete closing, activate pricing; reserve/sell unit, approve change order/payment certificate; approve/activate/close budget. Existing Finance reconciliation/tax/month closure invariants must be audited separately and preserved. No monetary threshold will be invented. Approval binds the scoped record, requested action and current content snapshot. Decision does not mutate the business record. Execution locks and consumes approval in the same transaction as business write, audit and projection validation. Human decision requires existing approval permission plus owner-domain write/lead authority; requester self-decision remains denied.

The final report must replace GAP with evidenced outcomes, retain deferred sources, cite exact pushed HEADs and CI, and recommend READY only after all closure checks pass.
