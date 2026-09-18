# Migrasi Tool, Governance, dan Release MVP-1

Batch ini direkonsiliasi dari `andara-alos-ai/alos` branch `develop` pada pinned SHA `01416390287114a451a22e16ff14e493df43362f`. Source dibaca melalui object Git dan tidak diubah.

## Keputusan migrasi

| Area legacy | Keputusan | Implementasi target |
|---|---|---|
| Tool Registry dan typed manifest | ADAPT | Registry Backend memiliki lifecycle, allowlist, permission, scope, timeout, idempotency policy, dan kill switch. Adapter dipilih dari registration Backend, bukan nama handler bebas dari GENESIS. |
| ToolExecutor berbasis `psycopg` dan handler bisnis monolit | SPLIT | Urutan validasi dan safe failure dipertahankan. Handler bisnis, query langsung, dan dependency monolit tidak disalin. Hanya diagnostic tool non-production yang tersedia. |
| Tool permission policy | ADAPT | `AuthorizationPolicy` memeriksa principal, tenant, organization, workspace, permission, dan scope secara deny-by-default. |
| Tool idempotency | ADAPT | Key diikat ke tenant, tool, dan digest immutable request. Replay identik tidak mengeksekusi adapter kembali; payload substitution ditolak. |
| Release governance | ADAPT | Canonical `ReleaseState`, Automated Assurance, AI ReviewPackage, IT Decision, Director Decision untuk perubahan material, release, activation, kill switch, dan rollback. |
| Audit tool/release | ADAPT | Setiap ToolRequest valid menghasilkan event request dan outcome. Setiap transisi release, kill switch, dan rollback menghasilkan append-only audit event dengan `correlation_id`. |
| Business tool handlers dan Agent runtime legacy | DO_NOT_MIGRATE | Tetap berada di domain pemilik atau GENESIS. Backend batch ini tidak mengaktifkan tool produksi. |

## Koreksi perilaku negative test

MVP-1 memiliki bypass berikut pada `AgentTestRunner`:

```python
if case.category == "NEGATIVE":
    passed = True
```

Perilaku tersebut tidak dimigrasikan. `AssuranceEvaluator` membandingkan status aktual, error code, dan fragmen alasan dengan expected behavior untuk semua kategori. Negative test yang memperoleh `SUCCESS` ketika mengharapkan `DENIED` akan gagal dan `AutomatedAssuranceReport` tidak dapat memajukan release.

## Flow authority

```text
GENESIS
  → ToolRequest
  → Backend internal API
  → contract validation
  → Tool Registry/lifecycle/allowlist
  → tenant + scope + permission policy
  → idempotency
  → Backend adapter
  → audit
  → ToolResult

Automated QA
  → AI ReviewPackage (advisory)
  → IT Decision (authoritative)
  → Director Decision bila material
  → Backend Release Authority
  → RELEASED → ACTIVE
```

AI review tidak dapat menghasilkan IT/Director approval, mengaktifkan release, membersihkan kill switch, atau melakukan rollback.

## Batasan baseline

- Idempotency store runtime masih process-local untuk deterministic test. Tabel PostgreSQL authoritative sudah didefinisikan pada migration `0003`; adapter SQL dan locking lintas process belum di-wire.
- Release authority domain memiliki adapter in-memory dan ORM/migration ownership. Transactional SQL repository dan endpoint human decision belum dibuat pada batch ini.
- Diagnostic echo tetap satu-satunya tool executable dan selalu non-production.
- Data legacy belum dimigrasikan; diperlukan mapping dan rehearsal terpisah.
