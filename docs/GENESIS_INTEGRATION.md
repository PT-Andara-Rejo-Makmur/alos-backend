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
