# MVP-2 Stage 2 — Authoritative Target Cascade

## Tujuan dan batas sistem

Stage 2 memindahkan planning state dari presentation model Stage 1 menjadi domain Backend yang contract-first, versioned, auditable, deterministic, dan persistent. Jalur authority tetap `ALOS Web → ALOS Backend Strategy Domain`; tidak ada panggilan Web ke GENESIS, provider AI, bank, social platform, GitHub, MCP, atau connector eksternal. GENESIS dan infrastructure tidak diubah.

Audit awal menemukan canonical `Principal`, workspace membership, `AuthorizationPolicy`, permission registry, append-only `AuditSink`, Evidence Registry, structured `PlatformError`, serta pola router/service/repository yang dapat digunakan kembali. Migration existing berakhir pada `0021_domain_indexes`; Finance, Sales, dan Property menggunakan persistence Backend tanpa memberi Web authority bisnis.

## Contract dan persistence

Canonical schema berada di `alos-contracts/schemas/strategy`, menggunakan Draft 2020-12, `additionalProperties: false`, dan identifier common. Public API versi 1.10.0 menjadi sumber endpoint. TypeScript generated projection menjadi seam Web; presentation type Stage 1 bukan canonical contract.

Migration append-only `0022_strategy_planning` membuat schema PostgreSQL `strategy` dan tabel:

- `plans`, `objectives`, `targets`, `target_observations`, `target_relationships`;
- `planning_assumptions`, `cascade_rules`, `cascade_runs`, `cascade_results`;
- `planning_constraints`, `constraint_results`, `kpi_definitions`, `initiatives`, `target_revisions`.

Semua record menyimpan tenant, organization, workspace, actor, timestamps, correlation ID, dan payload projection. Index mengikuti lookup scope, plan, lifecycle, cascade status, observation, exact relationship version, dan target revision. Relationship mempunyai unique constraint pada tenant, organization, type, parent version, dan child version. Migration tidak menyediakan destructive downgrade.

## Model, lifecycle, dan versioning

Plan bertipe `STRATEGIC_PLAN` (Renstra) atau `OPERATING_PLAN` (RKAP). Stable conceptual ID selalu dipasangkan dengan integer version. Lifecycle adalah `DRAFT → UNDER_REVIEW → APPROVED → ACTIVE`; `SUPERSEDED` dan `ARCHIVED` hanya historis. Ordinary editing hanya tersedia pada `DRAFT`. State approved/active tidak diubah in-place; target revision mempertahankan versi lama dan menghasilkan versi berikutnya sebagai `DRAFT`.

Target tidak menyimpan actual sebagai target. Observation membedakan `TARGET`, `ACTUAL`, `FORECAST`, dan `ASSUMPTION`, berikut period, unit, source, verification, evidence, observed/verified timestamps. Manual input memakai `MANUAL_EVIDENCED`; evidence wajib dan verification tidak pernah diinferensikan Web.

## Authority

| Operasi | Role | Permission | Scope |
|---|---|---|---|
| Read | role aktif | `strategy.read` | tenant + organization + workspace visibility |
| Company manage | `EXECUTIVE` | `strategy.company.manage` | company |
| Division draft | `WORKSPACE_LEAD` | `strategy.division.manage` | active workspace saja |
| Review | `BUSINESS_REVIEWER` atau `EXECUTIVE` | `strategy.review` | authorized scope |
| Approve | `EXECUTIVE` | `strategy.approve` | company |
| Activate | `EXECUTIVE` | `strategy.activate` | company |

Role tidak pernah cukup tanpa permission. Cross-tenant, cross-organization, dan mutation lintas workspace gagal dengan 403. Conflict lifecycle/version/graph gagal dengan 409. Backend adalah satu-satunya authority untuk action availability dan hasil mutation.

## Relationship dan graph

Relationship mendukung `CASCADE`, `CONTRIBUTES_TO`, dan `DEPENDS_ON` dengan exact target version. Service menolak self-reference, duplicate edge, cross-tenant/cross-org reference, dan cycle pada graph `CASCADE`. Cycle diperiksa kembali sebelum activation.

## Rule engine dan rounding

Engine hanya menerima rule bertipe dan parameter eksplisit:

- `DIRECT`: nilai input eksplisit;
- `SPLIT_FIXED`: fixed children dan optional exact rollup validation;
- `SPLIT_PERCENT`: parent dikali allocation ratio;
- `SUM_ROLLUP`: jumlah children;
- `RATIO_MULTIPLY`: input dikali ratio;
- `RATIO_DIVIDE_CEIL`: input dibagi ratio dan dibulatkan `CEILING`;
- `LIMIT_CHECK`: required dibanding available tanpa memutasi target.

Calculation memakai Python `Decimal`; tidak ada float, eval, arbitrary expression, JavaScript, atau LLM. Ratio canonical menggunakan decimal 0..1. Ratio missing menghasilkan `INCOMPLETE`; nol, negatif, atau lebih dari satu menghasilkan `INVALID`. Full allocation pada `SPLIT_PERCENT` harus berjumlah tepat `1`. Funnel Sales `LEAD → QUALIFIED → SURVEY → BOOKING_FEE → SPK → KPR_SUBMITTED → SP3K → AKAD` dapat menggunakan backward calculation `CEIL(downstream / conversion ratio)`. Trace menyimpan input, ratio, rounding, output target, status, dan message.

## Constraint, failure, evidence, dan audit

Constraint types adalah `INVENTORY`, `BUDGET`, `CAPACITY`, `DEPENDENCY`, `PERIOD`, `AUTHORITY`, dan `EVIDENCE`. Result adalah `PASS`, `FAIL`, `UNKNOWN`, atau `NOT_APPLICABLE`. Source yang hilang selalu `UNKNOWN`, bukan `PASS`. Critical `FAIL` dan critical `UNKNOWN` memblokir activation.

Evidence memakai reference ke Evidence Registry existing, bukan objek evidence duplikat. Mutation menghasilkan append-only audit event: plan create/update/submit/approve/activate, target create/observation/revision, relationship create, assumption create/verify, cascade preview/accept, dan constraint evaluation. Event mencatat actor, tenant, organization, workspace, subject, correlation ID, timestamp, outcome, dan metadata version/status.

## Cascade run

Preview menyimpan immutable input, assumption, rule, constraint, result snapshot beserta SHA-256 input/result hash dan exact root target version. Preview tidak memutasi target. Accept hanya menerima run `VALID`, memverifikasi scope, authority, plan version, serta kecocokan penuh output calculation, lalu menyimpan derived target sebagai `DRAFT`. Nilai hasil menjadi observation `TARGET` bertipe `SOURCE_LINKED`, terverifikasi, dan tertaut ke cascade run. Accept tidak approve atau activate. Activation kembali memvalidasi accepted run, calculation/constraint snapshot, graph, evidence, serta setiap observation TARGET; nilai null atau belum `VERIFIED` memblokir activation.

## API

Public API menyediakan Backend authority projection untuk collection actions, plan list/create/get dan version-safe PATCH untuk DRAFT, objective/target/relationship/assumption list-create, target detail dan observations, cascade preview/get/accept, plan submit/approve/activate, serta target revisions. HTTP route hanya parsing, principal binding, service invocation, dan projection. `PlatformError` menjaga 403/409 fail-closed tanpa membocorkan internal state.

## UI

Renstra menampilkan list, period/horizon, objectives, owner, lifecycle, sources, evidence, dan version. Form create hanya muncul dari collection capability Backend dan edit hanya dari action `EDIT` Backend. RKAP menampilkan header, objectives, targets, division allocation, assumptions, constraints, KPI, initiatives, evidence, dan approval state. Target tree memperlihatkan company-to-division hierarchy, exact versions, relationships, derivation, cascade, constraint, source/evidence, dan verification. Cascade preview memperlihatkan trace dan constraint `PASS/FAIL/UNKNOWN`, dengan satu action `Accept as Draft`; tidak ada activation otomatis atau calculation authoritative di browser.

## Test matrix dan vertical slice

Contracts menguji schema/reference, enum, ratio boundary, identifiers, exact version, manual evidence, dan extra properties. Backend menguji tujuh rule types, Decimal/CEILING, invalid/missing ratio, constraint states, lifecycle, immutability, revision history, tenant/permission/workspace denial, self-link/duplicate/cycle, preview non-mutation, accept-to-DRAFT, activation blocking, dan audit events. Web menguji routes, Backend state rendering, value-kind separation, constraints, null safety, fail-closed HTTP handling, serta dashboard regressions.

Vertical slice deterministik: Executive membuat Operating Plan DRAFT, objective dan corporate target; memasukkan evidenced assumption; membentuk rule/relationship; preview menghitung trace dan constraints; accept membuat division target DRAFT; submit, review/approve, activate; Executive membaca ACTIVE plan dan division hanya membaca assigned target dalam scope-nya. Tidak ada GENESIS dalam flow.

## Definition of Done dan Stage 3

Stage 2 selesai ketika contract canonical, migration, repository/service authoritative, deterministic cascade, constraints fail-closed, version history, RBAC, audit, connected Web views, vertical slice, dan seluruh quality gates hijau. Stage 3 dapat menambahkan source-linked ingestion/connectors, evidence verification workflow yang lebih luas, scheduler/cadence automation, dan AI advisory melalui Backend→GENESIS boundary. Stage 2 sengaja tidak mengimplementasikan connector, runtime AI calculation, automatic approval/activation, atau frontend business authority.
