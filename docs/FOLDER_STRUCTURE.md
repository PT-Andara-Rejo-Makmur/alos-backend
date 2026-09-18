# Struktur Folder

- `src/alos/api/public/`: endpoint yang boleh digunakan frontend dan product client.
- `src/alos/api/internal/`: endpoint service-to-service yang dilindungi authentication internal.
- `contracts/`: loader read-only untuk JSON Schema canonical dari artifact/check-out `alos-contracts`; tidak menyimpan salinan schema.
- `identity/`, `authentication/`, `authorization/`, `permissions/`, `security/`: principal, authentication, deny-by-default policy, permission, dan structured error.
- `tenants/`, `organizations/`, `workspaces/`: boundary hierarchical scope authoritative.
- `capabilities/`: registry definition capability-first yang tervalidasi, berversi, dan authoritative.
- `agents/`: registry definition, versi, dan authoritative run lifecycle Agent; tidak berisi
  runtime reasoning atau prompt orchestration.
- `skills/`: registry metadata/prosedur declarative dan versi Skill; tidak menjalankan unrestricted code atau reasoning implementation.
- `tools/`: contract validator, allowlist registry, policy, adapter, dan ToolExecutor.
- `governance/`: boundary policy, materiality, dan gate authoritative.
- `reviews/`, `approvals/`, `releases/`: reference assurance, keputusan authoritative, dan release lifecycle.
- `audit/`: persistence audit append-only.
- `evidence/`: boundary registry, lineage, provenance, dan validation evidence.
- `documents/`, `sources/`: metadata document dan source reference.
- `jobs/`: queue state machine, scheduler, bounded worker, retry/idempotency, dan stale-lease recovery; tidak memakai message broker.
- `notifications/`: notification intent dan provider boundary masa depan.
- `domains/`: boundary property, finance, sales, HR, legal, dan GIIVEPRO dengan authority ALOS yang sama.
- `integrations/genesis/`: client HTTP menuju AI Control Plane tanpa import kode GENESIS.
- `observability/`: correlation context dan OpenTelemetry API boundary.
- `persistence/`: SQLAlchemy base, engine/session lifecycle, serta model database.
- `migrations/`: revision Alembic append-only.
- `tests/unit/`: pengujian service dan configuration terisolasi.
- `tests/integration/`: pengujian HTTP boundary tanpa cloud dependency.
- `tests/security/`: authorization dan tenant isolation.
- `tests/contract/`: migration dan contract-boundary checks.
- `docs/`: panduan instalasi, operasi, pengembangan, database, integrasi, dan arsitektur.
- `.github/`: CI, CODEOWNERS, dan template pull request.
