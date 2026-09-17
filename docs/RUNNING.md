# Menjalankan Aplikasi

## Virtual environment

```powershell
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

```bash
# Linux/macOS
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Environment variable

- `APP_ENV`: `development`, `test`, `staging`, atau `production`.
- `APP_HOST` dan `APP_PORT`: alamat bind aplikasi.
- `DATABASE_URL`: wajib menggunakan `postgresql+asyncpg://`.
- `GENESIS_BASE_URL`: base URL internal GENESIS.
- `GENESIS_INTERNAL_TOKEN`: secret service-to-service; kosong hanya untuk development yang tidak memanggil GENESIS.
- `OTEL_SERVICE_NAME`: nama service telemetry.
- `ALOS_CONTRACTS_PATH`: path menuju checkout/artefak `alos-contracts`.
- `ENABLE_TEST_TOOLS`: hanya untuk pengujian non-production.

## Menjalankan Uvicorn

```bash
uvicorn alos.main:app --reload --host 127.0.0.1 --port 8000
```

Periksa `http://127.0.0.1:8000/health`, `/ready`, dan `/docs`.
