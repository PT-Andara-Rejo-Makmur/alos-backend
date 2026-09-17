# Database

ALOS Backend menggunakan PostgreSQL melalui SQLAlchemy Async dan driver `asyncpg`. Alembic menjadi satu-satunya mekanisme perubahan schema.

## Ownership schema

- `core`: authoritative business state milik Backend. Baseline menyimpan reference ReviewPackage, keputusan authoritative, dan release.
- `audit`: authoritative append-only audit milik Backend. Service code tidak menyediakan operasi update/delete.
- `ai_runtime`: bukan AI reasoning store milik Backend. Schema disediakan hanya untuk authoritative lifecycle/reference yang mungkin diperlukan kemudian.
- `research`: shared domain berbasis kontrak. Perubahan ownership harus eksplisit dan terdokumentasi sebelum tabel ditambahkan.

## Menjalankan migration

```bash
alembic upgrade head
alembic current
alembic history
```

Migration production bersifat append-only. Buat revision baru untuk koreksi; jangan mengedit revision yang sudah dirilis dan jangan melakukan downgrade production. Migration awal hanya membuat empat schema serta empat tabel authoritative yang benar-benar dibutuhkan foundation.
