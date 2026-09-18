# Database

ALOS Backend menggunakan PostgreSQL melalui SQLAlchemy Async dan driver `asyncpg`. Alembic menjadi satu-satunya mekanisme perubahan schema.

## Ownership schema

- `core`: authoritative business state milik Backend. Baseline menyimpan tenant, organization, workspace, actor/membership, RBAC grants, immutable Agent/Capability/Skill definition snapshots, reference ReviewPackage, keputusan authoritative, dan release.
- `audit`: authoritative append-only audit milik Backend. Service code tidak menyediakan operasi update/delete.
- `jobs`: queue, lease, retry, idempotency, dan safe failure state untuk worker Backend. Handler bisnis tetap berada pada module pemiliknya.
- `governance`: tool idempotency dan release lifecycle event authoritative. Tidak menyimpan AI reasoning.
- `ai_runtime`: bukan AI reasoning store milik Backend. Menyimpan authoritative Agent run
  lifecycle/reference, canonical request/result, registry digest, dan authorization snapshot;
  prompt, chain-of-thought, serta reasoning trace tidak disimpan di sini.
- `research`: shared domain berbasis kontrak. Perubahan ownership harus eksplisit dan terdokumentasi sebelum tabel ditambahkan.

## Menjalankan migration

```bash
alembic upgrade head
alembic current
alembic history
```

Migration production bersifat append-only. Buat revision baru untuk koreksi; jangan mengedit revision yang sudah dirilis dan jangan melakukan downgrade production. Revision `0001` membuat foundation review/decision/release/audit. Revision `0002` menambahkan identity hierarchy, RBAC, registry definition, job queue, dan field audit generik. Revision `0003` menambahkan Tool Registry persistence, idempotency, canonical release controls, dan lifecycle event tanpa mengambil ownership AI reasoning. Revision `0004` menambahkan authoritative Agent run lifecycle/reference pada schema `ai_runtime` tanpa menyimpan reasoning internal GENESIS. Revision `0005` menambahkan metadata dokumen, versi dokumen, registry sumber, versi sumber, dan evidence reference kanonis; schema `evidence` hanya menyimpan authority/provenance, bukan reasoning GENESIS.
