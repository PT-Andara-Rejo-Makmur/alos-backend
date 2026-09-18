# Integrasi GENESIS

## Alur frontend ke AI

```text
Frontend / ARA / GIIVEPRO
          |
          v
     ALOS Backend -- validasi tenant, scope, permission --> GENESIS
                                                        AI Control Plane
```

Frontend tidak pernah memanggil GENESIS langsung. Backend mengirim payload melalui typed internal contract dari `alos-contracts`, menyertakan service authentication dan `X-Correlation-ID`.

## Alur aksi Agent

```text
GENESIS / Agent
      |
      | ToolRequest
      v
ALOS Backend ToolExecutor
      | schema -> tenant/scope -> permission -> allowlist -> adapter -> audit
      v
ToolResult
```

GENESIS dan Agent tidak memperoleh credential atau koneksi database. Backend memvalidasi ulang seluruh context dan berhak menolak request.

`GenesisClient` menyediakan operasi health, agent run, factory analyze, research, dan AI review dengan timeout, structured error, serta correlation propagation. Endpoint yang belum tersedia di GENESIS akan menghasilkan `GenesisClientError`; Backend tidak menyediakan dummy business response.

## Diagnostic baseline

`GET /api/v1/system/integration` membuktikan jalur komunikasi tanpa LLM, provider API, atau business database. Backend membuat atau meneruskan `X-Correlation-ID`, memanggil `GET /internal/v1/system/integration` pada GENESIS, lalu memvalidasi payload internal dan payload publik menggunakan `schemas/common/integration-diagnostic.schema.json` dari `alos-contracts`.

Respons sukses mengembalikan `status: connected`, authority Backend, peran GENESIS sebagai `AI_CONTROL_PLANE`, dan correlation ID yang sama. GENESIS tidak tersedia menghasilkan `GENESIS_UNAVAILABLE`/`GENESIS_TIMEOUT` dengan HTTP 503. Payload GENESIS yang tidak sesuai contract menghasilkan `GENESIS_INVALID_RESPONSE` dengan HTTP 502. Semua kegagalan memakai structured error contract.

Contoh lokal:

```bash
curl -i -H "X-Correlation-ID: corr_manual_001" \
  http://127.0.0.1:8000/api/v1/system/integration
```
