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
- `CORS_ALLOWED_ORIGINS`: daftar origin Web publik yang dipisahkan koma. Default lokal hanya `http://127.0.0.1:3000` dan `http://localhost:3000`; jangan memakai wildcard pada deployment.
- `OTEL_SERVICE_NAME`: nama service telemetry.
- `ALOS_CONTRACTS_PATH`: path menuju checkout/artefak `alos-contracts`.
- `ENABLE_TEST_TOOLS`: hanya untuk pengujian non-production.

## Menjalankan Uvicorn

```bash
uvicorn alos.main:app --reload --host 127.0.0.1 --port 8000
```

Jalankan GENESIS pada port 8100 sebelum memeriksa jalur integrasi. Pastikan
`ALOS_CONTRACTS_PATH=../alos-contracts`, lalu periksa:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/ready`
- `http://127.0.0.1:8000/api/v1/system/integration`
- `http://127.0.0.1:8000/docs`
