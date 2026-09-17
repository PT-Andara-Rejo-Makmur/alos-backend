# Pengembangan

Gunakan application factory `alos.main:create_app` dalam pengujian agar configuration dapat di-inject tanpa environment global. Letakkan domain logic pada package pemiliknya, bukan di route. Route hanya melakukan transport mapping, authentication, authorization, dan memanggil service.

Aturan utama:

- Gunakan async pada I/O dan jangan melakukan blocking I/O di event loop.
- Jangan membuka koneksi database saat import module.
- Jangan mengimpor kode internal `genesis-ai`; gunakan `GenesisClient`.
- Jangan menduplikasi schema `alos-contracts` menjadi Pydantic model lokal.
- Semua mutasi lintas boundary harus memiliki correlation ID dan audit.
- Registry dan business domain baru harus berasal dari requirement, bukan spekulasi.

Jalankan quality gate sebelum pull request:

```bash
ruff check .
mypy
pytest
```
