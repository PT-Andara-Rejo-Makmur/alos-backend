# Authority Runtime MVP-1

Backend mengambil bagian authoritative dari legacy runtime pada pinned SHA
`01416390287114a451a22e16ff14e493df43362f`; reasoning loop tidak dipindahkan ke Backend.

`AgentRunAuthority` memvalidasi canonical AgentRunRequest, context tenant/organization/
workspace, Agent ID/version, registry lifecycle, permission refs, dan requested tool allowlist.
Run normal hanya dapat dimulai untuk Agent Registry version `ACTIVE`. Draft test hanya dapat
digunakan bila policy `allow_test_drafts` diaktifkan secara eksplisit.

Backend menyimpan state run authoritative, membuat runtime authorization snapshot, menerima
canonical AgentRunResult, memeriksa lineage/correlation, dan mencatat `run.started`,
`run.completed`, atau `run.failed` ke audit sink append-only.
`SqlAgentRunStore` dan migration append-only `0004_agent_run_authority` menyediakan persistence
PostgreSQL pada schema `ai_runtime`; in-memory store hanya untuk unit/local composition.

Execution business tool tetap mengikuti:

```text
GENESIS AgentRuntimeEngine
  -> POST /internal/v1/tool-requests
  -> contract validation
  -> identity / tenant / scope / permission / allowlist / kill switch
  -> Backend ToolExecutor
  -> canonical ToolResult
  -> GENESIS
```

Test `tests/integration/test_runtime_split_e2e.py` membuktikan run authority, internal HTTP
boundary, real Backend ToolExecutor diagnostic adapter, ToolResult, correlation, dan audit
dalam satu flow deterministic tanpa database atau provider credential.
