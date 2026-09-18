# ALOS Backend

`alos-backend` adalah core platform authoritative untuk ALOS, GENESIS, ARA, dan GIIVEPRO. Backend memiliki API, identity dan authorization enforcement, tenant/workspace scope, canonical state, registry platform, ToolExecutor, governance, approval, decision, release, audit, evidence, job lifecycle, persistence, serta boundary integrasi GENESIS.

Backend tidak melakukan AI reasoning, prompt orchestration, Agent planning, Skill reasoning, R&D reasoning, pemilihan model internal GENESIS, atau rendering frontend.

## Peran arsitektur

- Frontend, ARA, dan GIIVEPRO hanya memanggil ALOS Backend.
- GENESIS adalah AI Control Plane dan tidak dapat mengubah authoritative state secara langsung.
- Agent tidak menerima koneksi database; semua aksinya melewati ToolRequest dan Backend ToolExecutor.
- Permission, scope, tenant, dan workspace diberlakukan deny-by-default.
- AI recommendation berbeda dari keputusan IT/Director yang authoritative.
- Release lifecycle berbeda dari pembuatan atau eksekusi Agent.
- Schema lintas repository dimiliki `alos-contracts` dan tidak disalin ke sini.

Lihat [ARCHITECTURE.md](ARCHITECTURE.md) dan [Model Otoritas](docs/AUTHORITY_MODEL.md).

Hasil adaptasi authority dari MVP-1 dicatat pada [Migrasi Authority MVP-1](docs/MVP1_AUTHORITY_MIGRATION.md). Registry Backend selalu memvalidasi definition terhadap artifact `alos-contracts`; kode reasoning dan orchestration tidak ikut dimigrasikan.

Rekonsiliasi ToolExecutor, governance, negative-test behavior, kill switch, dan rollback tersedia pada [Migrasi Tool, Governance, dan Release MVP-1](docs/MVP1_TOOL_GOVERNANCE_RELEASE_MIGRATION.md).

Authoritative Agent run lifecycle berada pada `AgentRunAuthority`. Service ini memvalidasi
registry lifecycle, tenant/workspace, permission, tool intent, correlation, dan canonical
AgentRunResult; reasoning tetap pada GENESIS. Lihat
[Authority Runtime MVP-1](docs/MVP1_RUNTIME_SPLIT.md).

Pemisahan document/source/evidence authority dari intelligence dicatat pada
[Migrasi Knowledge dan Research MVP-1](docs/MVP1_KNOWLEDGE_RESEARCH_MIGRATION.md).

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy Async, Alembic, PostgreSQL, pytest, Ruff, mypy, OpenTelemetry API boundary, Docker, dan GitHub Actions.

## Prasyarat

- Python 3.12
- PostgreSQL untuk migrasi dan runtime persistence
- Git
- Docker opsional
- Checkout atau artefak rilis `alos-contracts` untuk validasi runtime ToolRequest

## Instalasi Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Instalasi Linux/macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Sesuaikan `.env` dengan environment lokal. Jangan menyimpan `.env` atau secret ke Git. Daftar variabel dijelaskan di [Menjalankan Aplikasi](docs/RUNNING.md).

## Database dan migrasi

Buat database PostgreSQL, tetapkan `DATABASE_URL`, lalu jalankan:

```bash
alembic upgrade head
```

Migration bersifat append-only. Ownership schema `core`, `audit`, `evidence`, `ai_runtime`, dan
`research` dijelaskan di [Database](docs/DATABASE.md).

## Menjalankan secara lokal

```bash
uvicorn alos.main:app --reload --host 127.0.0.1 --port 8000
```

Endpoint minimum:

- `GET /health`
- `GET /ready`
- `GET /api/v1/system/integration` untuk diagnostic Web → Backend → GENESIS berbasis contract
- `GET /api/v1/system/info`
- `GET /docs` untuk OpenAPI interaktif
- `GET /openapi.json` untuk dokumen OpenAPI otomatis
- `/internal/v1/...` sebagai namespace internal yang memerlukan token

Boundary `POST /internal/v1/tool-requests` menerima canonical `ToolRequest` dari GENESIS dan hanya
menjalankan adapter setelah contract, context, permission, scope, registry, dan allowlist lulus.
Lihat [Boundary Eksekusi Tool](docs/TOOL_EXECUTION_BOUNDARY.md).

## Quality gate

```bash
ruff check .
mypy
pytest
python -c "from alos.main import app; assert app.title == 'ALOS Backend'"
```

Pengujian unit tidak memerlukan database atau cloud secret. Lihat [Pengujian](docs/TESTING.md).

## Docker

```bash
docker build -t alos-backend:local .
docker run --rm --env-file .env -p 8000:8000 alos-backend:local
```

Container berjalan sebagai non-root. PostgreSQL dan `alos-contracts` harus disediakan sesuai lingkungan deployment; image ini tidak membuat service tambahan.

## Struktur proyek

Kode aplikasi berada di `src/alos`, migration di `migrations`, pengujian di `tests`, dan dokumentasi operasi di `docs`. Penjelasan lengkap tersedia di [Struktur Folder](docs/FOLDER_STRUCTURE.md).

## Hubungan lintas repository

- `alos-contracts`: source of communication truth untuk schema, event, dan OpenAPI lintas repository. Backend memvalidasi contract artifact, bukan menduplikasinya.
- `genesis-ai`: AI Control Plane yang dipanggil melalui client HTTP internal bertipe. Lihat [Integrasi GENESIS](docs/GENESIS_INTEGRATION.md).
- `alos-web`: hanya menggunakan API publik Backend dan tidak boleh memanggil GENESIS secara langsung.
- `alos-infra`: mengonfigurasi database, secret, telemetry SDK/exporter, dan deployment tanpa mengubah semantik aplikasi.

## Catatan keamanan

- Authorization deny-by-default dan mengikat actor ke tenant serta workspace.
- Internal API memerlukan service token; token direpresentasikan sebagai `SecretStr` dan tidak diserialisasi.
- Correlation ID dipropagasikan pada response, ToolExecutor, audit, dan panggilan GENESIS.
- Diagnostic echo tool dilabeli **NON-PRODUCTION TEST TOOL** dan konfigurasi menolaknya di production.
- Agent dan GENESIS tidak memperoleh koneksi database.
- OpenTelemetry SDK/exporter dikonfigurasi oleh deployment, bukan dengan hard-coded credential.

Panduan tambahan: [Instalasi](docs/INSTALLATION.md), [Pengembangan](docs/DEVELOPMENT.md), dan [Kontribusi](CONTRIBUTING.md).
