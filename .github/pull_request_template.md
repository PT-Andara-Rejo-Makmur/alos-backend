## Task ID (wajib)

Task ID:

## Ringkasan

Jelaskan perubahan, authority boundary yang terdampak, dan alasan perubahan.

## Pemeriksaan arsitektur

- [ ] Backend tetap menjadi source of truth.
- [ ] Tidak ada akses database langsung dari Agent/GENESIS.
- [ ] Aksi Agent tetap melalui ToolExecutor.
- [ ] Tidak ada schema `alos-contracts` yang diduplikasi.
- [ ] Permission, scope, dan tenant tetap deny-by-default.

## Validasi

- [ ] Ruff lulus
- [ ] mypy lulus
- [ ] pytest lulus
- [ ] Migration bersifat append-only atau tidak berlaku
- [ ] Tidak ada secret atau data produksi
