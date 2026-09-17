# Pengujian

```bash
pytest
```

Suite foundation meliputi health, readiness, system info, internal authentication, typed configuration, authorization deny-by-default, isolasi tenant, penolakan tool yang tidak dikenal, correlation dan audit ToolExecutor, structured error GENESIS, serta import migration dan ownership tabel.

Pengujian unit dan contract tidak memerlukan PostgreSQL atau cloud secret. Integration test database masa depan harus menggunakan database sementara yang terisolasi dan tidak boleh menggunakan production data.

Quality gate lengkap:

```bash
ruff check .
mypy
pytest
python -c "from alos.main import app; assert app.title == 'ALOS Backend'"
```
