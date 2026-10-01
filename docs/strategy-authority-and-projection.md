# Strategy authority and Executive projection

Strategy remains authoritative. Executive reads Strategy through its service/repository boundary and owns no persistence or mutations. Shared Work and other domains are not aggregated.

## Lifecycle and versions

Plans follow DRAFT -> UNDER_REVIEW -> APPROVED -> ACTIVE. Only an ACTIVE version can be archived. Activating the latest approved version supersedes previously active versions of the same plan identity, including their objectives and targets. Different plan identities/periods are not made mutually exclusive: the existing model does not define that policy.

Ordinary mutation requires the owner to match the active workspace, the exact tenant/organization, and both a permitted role and action permission. Company cascade is the explicit governed allocation path to an existing active workspace in the same tenant/organization. IT_ADMIN is not a business superuser.

A target revision retains v1 ACTIVE and creates v2 DRAFT with its reason and immutable revision proposal. The proposed version follows submit/approve/activate independently under its ACTIVE plan. Only valid activation supersedes the older active target. Proposal state is a retained historical record; current lifecycle belongs to the proposed target version and transition audit. Exact-version reads and observations preserve original lineage. Historical superseded/archived targets cannot be edited or verified.

## Observation matrix

| Kind | Allowed target lifecycle | Meaning |
| --- | --- | --- |
| TARGET | DRAFT | Planning value; frozen while parent plan is under review/approved |
| ACTUAL | ACTIVE | Recorded performance; does not edit target definition |
| FORECAST | DRAFT, ACTIVE | Separate monitoring estimate; never substitutes TARGET or ACTUAL |
| ASSUMPTION | DRAFT | Planning observation under planning authority |

A DRAFT target under an ACTIVE plan must have a retained revision. All observations are inserted, never merged or overwritten. Server-assigned recorded_at and monotonically increasing record_sequence make selection deterministic even with equal clock timestamps. Monitoring selection uses latest observed_at then record_sequence; planning selection uses recorded_at then record_sequence. Verification appends a superseding observation with the original value/source/evidence, a terminal decision and reason. A newer unverified record does not fall back to an older verified record.

MANUAL_EVIDENCED requires evidence references. SOURCE_LINKED requires a nonblank authoritative reference. The implemented Strategy source adapter is an accepted cascade with matching target/version/output. External operational adapters are unavailable and rejected; arbitrary strings are not authoritative sources.

EXECUTIVE plus strategy.review authorizes planning verification in the active owner workspace. Monitoring verification requires the separately resolved strategy.observation.verify permission and EXECUTIVE or DIVISION_LEAD in that workspace; it is not granted by default merely because an actor is Executive. Existing governed membership permission state can carry the grant. Neither client verification timestamps nor role-only assertions confer verification authority.

## Performance

The Backend compares latest verified TARGET and ACTUAL for the exact target version, unit and period only after the period has ended, on an ACTIVE target, with finite non-null values. HIGHER_IS_BETTER uses actual >= target; LOWER_IS_BETTER uses actual <= target; EXACT uses equality; BINARY requires boolean equality. A zero supplied by the source is a value; an absent value stays unknown. Optional display labels do not change scope or period identity. Assumptions reject nonfinite/unrepresentable numbers and enforce their canonical unit range.

RANGE lacks canonical bounds, PERCENTAGE and RATIO lack canonical direction, MILESTONE lacks a completion policy, and CUMULATIVE lacks an aggregation policy. These return NOT_EVALUATED. Insufficient/unverified/unfinished inputs also return NOT_EVALUATED. No thresholds, risk scores, forecast substitutions or domain metrics are invented.

## Cascade

Preview resolves verified versioned Strategy observations and verified versioned assumptions, validates rule allocations, and retains their context. Rollup accepts only declared source targets; other rules require one source. Unlinked input keys and nonfinite numbers are rejected. Human metadata may be supplied as canonical derived_targets; otherwise the response explicitly lists required_metadata and cannot be accepted by the Web. The rule output ID/version must match the eventual candidate.

Input hash binds inputs, rules, assumptions, constraints, complete candidate metadata and source context. Result hash binds calculation and constraint results. Acceptance validates both retained hashes, optional client hashes, unchanged current sources, complete exact candidate IDs/versions, authority and boundaries. Duplicate acceptance conflicts. Targets, SOURCE_LINKED TARGET observations, retained accepted run and audit are committed in one PostgreSQL transaction under an organization advisory lock. Audit failure rolls back the entire operation.

## Executive API

GET /api/v1/executive/overview requires EXECUTIVE plus strategy.read. It returns the canonical ExecutiveOverviewProjection with strategy_data from authoritative Strategy state. CONNECTED means retrieval succeeded with data; CONNECTED_EMPTY means retrieval succeeded with no data; ERROR means retrieval failed. Other integrations report UNAVAILABLE. Timestamps use known persisted Strategy updates only, and remain null when unknown. No Executive table, synchronization job or copied operational data exists.

## Persistence and contracts

Existing Strategy JSON payloads accommodate the added metadata and immutable history fields. Legacy fields remain optional/nullable. No migration is needed, and old migrations are unchanged. Canonical schema validation precedes request-to-domain conversion through CanonicalContractCatalog. Generated Python/TypeScript are produced by the existing generators. Cascade response collection tightening is explicitly recorded in the existing compatibility decision ledger and shipped with Backend/Web consumers.
