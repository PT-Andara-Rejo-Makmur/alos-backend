# ARA conversation authority

The public `/api/v1/ara` API accepts minimal user intent and requires the current authenticated Principal.
Threads are PostgreSQL records scoped by tenant, organization, workspace and actor. Every message/run lookup
first authorizes its parent thread; invisible records return 404. A locked active-run reservation prevents concurrent
messages in one thread. Messages and structured responses survive process/session refresh. Long histories are bounded
to the most recent 500 messages, returned in chronological order. The first question supplies a default thread title.

ContextBundleBuilder intersects the registered active tool catalog with Principal permissions/scopes and caps budgets.
Role names cannot grant RESTRICTED classification; the exact Backend `restricted.access` grant is required.
ToolExecutor rechecks identity, role, data scope, division, project, classification, lifecycle, kill switches and run allowance.
Business adapters call existing owner services with bounded arguments; they contain no SQL or write actions.

ARA uses the existing AgentRunAuthority and GENESIS AgentRuntimeEngine in explicit TEST mode. Development requires
ENABLE_TEST_TOOLS; staging/production do not enable the deterministic route. No production model is connected.
The whole conversation workflow has a 30-second deadline, at most 16 steps, 12 tool calls, 12,000 tokens and zero model cost.
Only authorized research may invoke one narrowed business reader child, with depth 1, one tool, 1,000 tokens and a 10-second deadline.
Backend issues the child context and the existing run authority enforces its parent lineage and inheritance.
Cancellation is checked through the existing internal probe; cancellation received before completion wins over a late completed result.

ToolExecutor registers real evidence with source, content hash, capture time, run and correlation. GENESIS verifies dynamic
evidence identity and hash before admitting it. Tool/record content is data and cannot alter instructions. Failed reads return
FAILED with actual failed tool identifiers; unavailable services return 503 with a persisted FAILED response. Unknown values remain null.

Conversation history is separate from reusable governed memory. Only evidence-backed memory with matching actor/thread,
scope, classification and identity enters ARA context. Memory can select a prior domain for a follow-up; current facts are always
read again. Chat is never automatically promoted to organizational memory. Existing authorized organizational memory remains separate.

Internal research requires research.request and research.management, references current evidence, and persists canonical findings,
recommendations and DRAFT backlog candidates through the existing research persistence model. Factory proposals call the existing
orchestrator with proposal_only=true and never register a definition. ReviewPackage is advisory and references the captured proposal,
factory draft or research result. No IT/Director decisions, releases, activation or business writes occur in ARA.

Validation: PostgreSQL conversation tests, the curated tool authority matrix, scoped memory tests, foundation regressions,
and `alos-infra/scripts/verify-ara-roundtrip.py` prove real authenticated ASGI boundaries. The Docker/BFF smoke proves deployment topology.
