# Arsitektur ALOS Backend

## Posisi sistem

```text
alos-web / ARA / GIIVEPRO
             |
             | API publik
             v
       ALOS Backend  <---- PostgreSQL authoritative state
             |
             | typed internal HTTP contract
             v
          GENESIS ----> ModelGateway
             |
             | ToolRequest
             v
       Backend ToolExecutor ----> adapter bisnis yang diizinkan
```

ALOS Backend merupakan source of truth. GENESIS menghasilkan reasoning dan recommendation, sedangkan Backend memvalidasi context, permission, scope, tool allowlist, keputusan, serta release transition.

## Trust boundary

Request publik tidak dipercaya sampai authentication dan authorization selesai. Request internal tetap tidak dipercaya hanya karena berasal dari jaringan internal; service token, contract, correlation, tenant, scope, dan permission tetap diverifikasi. Tool adapter tidak dapat dipanggil melewati ToolExecutor.

## Komponen foundation

- API publik untuk frontend dan produk.
- API internal yang dilindungi token untuk komunikasi service-to-service.
- Authorization policy deny-by-default.
- ToolExecutor dengan contract validation, policy, adapter, dan audit.
- Client HTTP GENESIS dengan timeout dan structured error.
- SQLAlchemy Async dan Alembic untuk PostgreSQL.
- Model terpisah untuk AI recommendation, keputusan IT/Director, dan release decision.
- Correlation middleware dan OpenTelemetry API boundary.

## Batas versi foundation

Registry capability/agent/skill, governance gate, evidence pipeline, job scheduler, notification provider, serta business domain hanya memiliki package boundary. Implementasi spekulatif tidak ditambahkan sebelum requirement tersedia.
