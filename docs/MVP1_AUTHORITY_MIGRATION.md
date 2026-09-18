# Migrasi Authority MVP-1

Batch ini direkonsiliasi dari `andara-alos-ai/alos` branch `develop` pada pinned SHA `01416390287114a451a22e16ff14e493df43362f`. Repository sumber hanya dibaca dan tidak diubah.

## Modul yang dimigrasikan

| Sumber legacy | Target | Keputusan |
|---|---|---|
| `identity/models.py` dan actor context | `identity/`, `authentication/principal.py` | ADAPT: hierarchy tenant/organization/workspace/membership dan canonical `actor_id`; token claim/JWT lokal tidak disalin. |
| `authorization.py` | `authorization/policy.py` | REUSE/ADAPT: deny-by-default, tenant, organization, workspace, permission, dan scope equality/subset check. |
| `permissions/registry.py` | `permissions/registry.py` | ADAPT: RBAC grant tenant-aware; direct `psycopg`, agent-key alias, dan approval business logic tidak disalin. |
| `security/` | `authentication/internal.py`, `security/errors.py` | ADAPT: constant-time internal token, structured safe error, dan correlation; local token issuer legacy tidak menjadi production auth. |
| `audit/` | `audit/models.py`, `audit/repository.py` | ADAPT: generic append-only event dan tenant/workspace query; tidak ada update/delete. |
| `persistence/` dan schema authority legacy | SQLAlchemy Async models + Alembic `0002` | ADAPT: application migration kembali ke owner Backend; raw synchronous `psycopg` di domain dihilangkan. |
| `jobs/` | `jobs/models.py`, `repository.py`, `scheduler/`, `worker/`, `recovery/` | ADAPT: idempotency, bounded retry, lease, safe error, dan recovery dipertahankan tanpa Celery/Redis/Temporal. |
| Authority part dari `agents/registry.py` | `agents/registry/` | SPLIT: immutable definition/version/lifecycle ke Backend; draft generation, prompt, dan AI reasoning tetap di GENESIS. |
| Authority part dari `capabilities/registry.py` | `capabilities/registry/` | SPLIT: authoritative registry ke Backend; discovery/resolution intelligence tetap di GENESIS. |
| Skill authority yang belum mandiri di MVP-1 | `skills/registry/` | Contract-driven baseline: Backend menyimpan definition/version yang telah divalidasi; tidak mengklaim legacy runtime skill tersedia. |

## Contract dan lifecycle

Backend membaca JSON Schema dari path `ALOS_CONTRACTS_PATH` melalui `CanonicalContractCatalog`; schema tidak diduplikasi. Registry menerima `AgentDefinition`, `CapabilityDefinition`, atau `SkillDefinition`, menyimpan snapshot immutable, dan mencatat digest serta audit event.

Urutan authoritative adalah `DRAFT -> APPROVED -> ACTIVE`. Approval memerlukan authority IT atau Director dan `decision_id`; activation memerlukan `release_id`. AI review atau recommendation tidak dapat menjadi keputusan.

## Yang tidak dimigrasikan

- ModelGateway dan provider adapter.
- Prompt template generation atau Agent planning.
- GENESIS chat, routing, orchestration, semantic/research reasoning.
- Direct database access dari Agent atau GENESIS.
- Business job handler monolith untuk laporan/notifikasi.
- Local bootstrap users, hard-coded divisions, dan local JWT issuance sebagai auth produksi.

## Dependency yang belum diselesaikan

- Service registry dan job state machine memiliki deterministic in-memory adapter untuk unit test serta SQLAlchemy model/Alembic schema. Wiring repository SQL penuh dan integration test PostgreSQL dilakukan pada batch persistence/cutover, bukan dengan membawa raw `psycopg` legacy.
- Identity authentication pengguna akhir belum dipilih; batch ini menetapkan principal/authority boundary, bukan identity provider baru.
- Legacy data migration dari UUID/key/table lama ke canonical IDs memerlukan rencana data mapping dan rehearsal tersendiri.
- Package/artifact distribution resmi `alos-contracts` belum tersedia; sementara ini `ALOS_CONTRACTS_PATH` menunjuk checkout/artifact yang dipasang operator.
