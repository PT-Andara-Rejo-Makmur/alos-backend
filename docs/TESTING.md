# Pengujian

```bash
pytest
```

Suite foundation meliputi health, readiness, system info, internal authentication, typed configuration, authorization deny-by-default, isolasi tenant, penolakan tool yang tidak dikenal, correlation dan audit ToolExecutor, structured error GENESIS, serta import migration dan ownership tabel.

Pengujian unit dan contract tidak memerlukan PostgreSQL atau cloud secret. Integration test
SQL sudah tersedia dan membutuhkan PostgreSQL serta `ALOS_TEST_DATABASE_URL` yang menunjuk
server disposable. Fixture membuat/menghapus database tes dan memigrasikannya; jangan
menggunakan server atau data operasional. Jalankan fixture dengan nama database yang sama
secara berurutan. Tidak adanya PostgreSQL atau tes yang skipped bukan bukti integration PASS.

Contoh pemilihan unit tests tanpa database:

```bash
pytest tests/unit tests/contract
```

Untuk integration, set `ALOS_TEST_DATABASE_URL` secara privat, lalu jalankan `pytest` atau
subset yang sesuai. Regresi Temuan/Tugas berada pada
`tests/integration/test_finding_corrective_action.py`, `test_finding_lifecycle_api.py`
dan `test_shared_work_members.py`. Pemeriksaan model nyata menggunakan script opt-in
di Infra; pytest tidak memerlukan API key provider.

Quality gate lengkap:

```bash
ruff check .
mypy
pytest
python -c "from alos.main import app; assert app.title == 'ALOS Backend'"
```
