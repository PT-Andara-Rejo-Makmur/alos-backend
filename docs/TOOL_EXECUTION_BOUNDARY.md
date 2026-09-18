# Boundary Eksekusi Tool

## Alur authoritative

```text
GENESIS
  → ToolRequest
  → POST /internal/v1/tool-requests
  → validasi contract
  → tenant/organization/workspace/actor policy
  → permission dan scope
  → registry dan allowlist
  → lifecycle dan kill switch
  → validasi input tool
  → idempotency policy
  → adapter Backend
  → audit
  → ToolResult
  → GENESIS
```

Endpoint tersebut merupakan internal service boundary dan dilindungi service token. Endpoint tidak
tersedia pada namespace public. `correlation_id` pada header, `ExecutionContext`, `ToolResult`, error,
dan audit harus sama. Perbedaan correlation ID menghasilkan `DENIED`.

## Tanggung jawab

- **GENESIS:** membentuk dan memvalidasi `ToolRequest`, mengirimnya ke Backend, lalu memvalidasi
  `ToolResult`. GENESIS tidak memuat adapter tool atau koneksi business database.
- **Authorization dan policy:** ALOS Backend mengikat actor ke tenant, organization, dan workspace,
  lalu menerapkan permission/scope secara deny-by-default.
- **Execution:** hanya `ToolExecutor` Backend yang dapat memilih registration dan menjalankan adapter.
- **Audit:** Backend menulis event `REQUESTED` dan terminal outcome (`SUCCESS`, `DENIED`, `REJECTED`,
  `FAILED`, atau `TIMEOUT`). Baseline menggunakan sink in-memory; production wajib menggantinya
  dengan authoritative audit repository.
- **Idempotency:** key diikat ke tenant, tool, dan digest immutable request. Replay yang identik
  menggunakan output tersimpan; penggunaan key yang sama untuk payload berbeda menghasilkan
  `IDEMPOTENCY_CONFLICT`.
- **Error handling:** malformed contract menghasilkan structured HTTP error. Outcome setelah request
  valid selalu dikembalikan sebagai canonical `ToolResult`.

## Diagnostic tool

`diagnostic.echo` adalah **NON-PRODUCTION TEST TOOL** tanpa I/O dan tanpa side effect. Tool hanya
di-allowlist ketika `ENABLE_TEST_TOOLS=true`, dan konfigurasi tersebut dilarang pada production.
Input yang diterima hanya `{ "message": "..." }` dengan panjang 1–256 karakter.

Tool Registry menggunakan lifecycle `DRAFT → APPROVED → ACTIVE`. Maker tidak boleh menyetujui
tool miliknya sendiri. State non-active, allowlist denial, atau kill switch selalu menghentikan
eksekusi sebelum adapter dipanggil.
