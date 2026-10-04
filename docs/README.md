# Indeks Dokumentasi alos-backend

Mulai dari [README repository](../README.md). Panduan runtime mengikuti source
dan contracts terkini; requirements/compatibility bukan klaim seluruh fitur siap.
Bukti tes bertanggal hanya berlaku untuk source dan environment yang dicatat.
Status lintas repository dipusatkan pada [readiness produksi](https://github.com/PT-Andara-Rejo-Makmur/alos-infra/blob/development/docs/PRODUCTION_READINESS_2026-10-04.md).

Authority tetap Web → Backend → GENESIS, dengan Backend sebagai pemilik data,
akses dan keputusan. Secret, dump, log privat dan artifacts lokal tidak masuk Git.

## Runtime, operasi dan authority

- [Authority Percakapan ARA](ara-runtime.md)
- [Model Otoritas](AUTHORITY_MODEL.md)
- [Operasi bisnis dan kewenangan](business-operations.md)
- [Validasi operasi bisnis ALOS](business-validation.md)
- [Identity and Access](CANONICAL_IDENTITY_ACCESS.md)
- [Database](DATABASE.md)
- [Pengembangan](DEVELOPMENT.md)
- [Domain Data API](DOMAIN_DATA_API.md)
- [Executive authority and Strategy contracts](EXECUTIVE_AUTHORITY.md)
- [Struktur Folder](FOLDER_STRUCTURE.md)
- [Integrasi GENESIS](GENESIS_INTEGRATION.md)
- [Initial identity administrator bootstrap](INITIAL_IDENTITY_BOOTSTRAP.md)
- [Instalasi](INSTALLATION.md)
- [Menjalankan Aplikasi](RUNNING.md)
- [Pengujian](TESTING.md)
- [Boundary Eksekusi Tool](TOOL_EXECUTION_BOUNDARY.md)

## Spesifikasi bisnis dan Strategy

- [Canonical business coverage](canonical-business-coverage.md)
- [Legal, HR and IT canonical operations](legal-hr-it-operations.md)
- [MVP-2 Stage 2 — Authoritative Target Cascade](mvp2-stage2-target-cascade.md)
- [Strategy authority and Executive projection](strategy-authority-and-projection.md)


## Pemeriksaan sebelum commit

Dari sibling checkout Infra, jalankan `python scripts/verify-documentation.py`.
Pemeriksaan memvalidasi link file kelima repository serta casing Linux tanpa jaringan.
