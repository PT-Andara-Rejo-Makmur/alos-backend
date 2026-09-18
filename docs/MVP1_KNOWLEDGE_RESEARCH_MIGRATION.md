# Migrasi Knowledge dan Research MVP-1

Audit menggunakan snapshot `andara-alos-ai/alos@01416390287114a451a22e16ff14e493df43362f`.
Modul legacy `documents/center.py`, `documents/intelligence.py`, dan `sources/registry.py`
sebelumnya mencampur persistence, authorization, audit, retrieval, dan intelligence.

Backend kini memiliki authority untuk:

- metadata dokumen dan versi immutable;
- registry sumber, versi, klasifikasi immutable, dan verifikasi manusia;
- evidence reference kanonis;
- enforcement tenant, organisasi, workspace, scope, permission, dan classification clearance;
- audit operasi register, verify, retrieve, deny, success, dan failure;
- migration append-only `0005_knowledge_authority` untuk tabel authority.

Retrieval dari GENESIS harus melalui `source.search_context` sebagai adapter read-only di belakang
ToolExecutor. Adapter mengembalikan `ContextBundle` dengan sitasi baris, content hash, versi sumber,
dan evidence lineage. Tool hanya dapat aktif setelah terdaftar, allowlisted, serta lolos permission
`sources.read` dan scope `scope.sources.read`.

Implementasi service saat ini memakai store in-memory untuk unit/integration test. Model SQL dan
migration authority sudah tersedia, tetapi wiring repository SQL ke service runtime merupakan
tahap berikutnya. Content binary/object storage tetap berupa integration boundary; tidak disimpan
sebagai fake object-storage implementation.

Document comparison, semantic analysis, memory ranking, dan research reasoning tidak dimiliki
Backend dan berada di GENESIS.
