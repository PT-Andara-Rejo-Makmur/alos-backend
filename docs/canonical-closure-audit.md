# Canonical closure audit

## Historical pre-implementation audit

The snapshot and GAP decisions in this section describe the state before migrations
0029–0031 and their implementations. They are retained as historical evidence;
they are not the current migration head or current capability status.

Historical audit date: 2026-10-02. All repositories were fetched; `development` and working trees were clean.

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

## Current acceptance audit — 2026-10-02

The final-acceptance checkout was fetched on `development` at the following heads:
contracts `fc5c08854c6f1ce17e9efe0adfee3b2a98aab1bc`, backend
`be3ccbb61e82efd49556cbd71634dc25b9d87c33`, web
`7f91feabb43163e4655c554014c6e6933c861ea1`, infra
`1db7d3a87afb7426255fe2e0ff58df8b6e578c49`, and read-only GENESIS
`1ffb055a82cc04d668a7582c0a38af03614fcb57`.

The current linear, append-only migration head is
`0031_legal_reviews_revisions`, following `0028_password_reset_challenges`,
`0029_material_approvals`, and `0030_ga_records`. Fresh and incremental
PostgreSQL migration verification passed. No historical migration was edited.

| Previously recorded GAP | Current implementation and acceptance evidence |
|---|---|
| Action-scoped material approvals | Implemented. All 11 owner actions passed browser request, independent decision, explicit execution, PostgreSQL state/audit/consumption verification, readback, refresh, and consumed-replay rejection. PROJECT/TASK compatibility remains covered by the existing integration suite. |
| Facility request | Implemented. Browser create and explicit IN_PROGRESS/COMPLETED lifecycle passed; completion without resolution notes was rejected. |
| Recorded inventory, asset handover, maintenance, service assessment | Implemented. Browser creation, immutable event rejection, scoped persistence, atomic audit, readback, and refresh passed. These records do not infer readiness or silently mutate Identity/Finance. |
| Legal Review | Implemented. Browser create and review lifecycle passed; missing assessment and unauthorized reviewer were rejected. REVIEWED left the associated contract in DRAFT. |
| Contract revision | Implemented. Browser creation references an existing immutable Document Version; canonical persistence, audit, readback, refresh and prohibited PATCH were verified. Signature/execution authority remains unavailable. |

The initial automated checkout gates passed: 208 Contracts tests, 639 Backend
tests, and 492 Web tests. The migration subset is included in the Backend count.
The isolated integration smoke also passed, including deterministic Backend ↔
GENESIS behavior. Initial browser acceptance covered creation of 89 business
resource types, all 11 material actions, and scoped security negatives.

The first defect register was completed before runtime code changes. It recorded
one P1 Identity HR workspace permission defect, two P2 Web form defects, and two
P3 presentation/documentation defects. HR_GA/HRGA division aliases are normalized
to the existing HR permission policy; this does not grant IT or Executive business
write authority. Existing memberships are not silently rewritten: any access
correction must use the governed Identity membership flow.

The fresh final rerun used Backend runtime commit `3c7932b` and Web `c07914f`.
Package-wide verification passed: 208 Contracts, 647 Backend, and 503 Web tests
(1,358 total, without double-counting targeted/migration subsets). Ruff, Mypy,
schema/examples/OpenAPI/generation/compatibility, lint, typecheck, production
build, all Compose configurations, 26 topology invariants, restore proof,
fresh migration and deterministic Backend ↔ GENESIS integration passed.

The final browser/API acceptance evidence contains 429 passing case results,
including 89 business resource creation chains, 64 update/retained-lock checks,
89 security boundary bundles (1,335 negative HTTP requests), all 11 material
actions, GA/Legal lifecycles, Identity governance, Strategy and Shared Work.
Each business resource creation was checked through the UI/BFF request, scoped
PostgreSQL row and audit, API projection and refreshed UI. Controlled atomic
rollback, stale approval, concurrency, period locking, partial-source errors and
cross-domain reference rejection additionally passed the real PostgreSQL suite.
Credential setup/reset have API/BFF and PostgreSQL integration evidence; these
are not described as browser DOM credential-entry acceptance.

The supplemental register recorded two P2 Identity presentation defects and one
P1 membership dialog initialization defect before their fixes. The Web fix
refreshes the selected account, explains unavailable supporting-evidence capture,
and initializes add/edit/revoke dialogs from their actual selected membership.
The final browser rerun verified add/edit/live detail/refresh, active workspace
selection, retained membership revocation, specific-session revocation, account
suspension/reactivation and secret-free history. Suspended login correctly
returns the canonical 401 INVALID_CREDENTIALS without exposing account state.

All eight registered defects are closed: initial P0/P1/P2/P3 counts were
0/2/4/2; remaining counts are 0/0/0/0. Final evidence files are
`FINAL-ACCEPTANCE-REPORT.md`, `FINAL-ACCEPTANCE-MATRIX.md`,
`defect-register-closed.md`, and the corresponding `*-closure.json` case results
in the local acceptance artifact directory dated 2026-10-02. The final report
records exact final pushed HEADs and CI results separately, including the
documentation-only closure commit, so those hashes are not self-referential.

Deferred external connectors, Strategy Initiative execution without its canonical
integration, HR final decisions without authority, verified Legal signature/
execution, and AI production capabilities retain their existing unavailable or
deferred status. No historical migration or GENESIS source was changed.
