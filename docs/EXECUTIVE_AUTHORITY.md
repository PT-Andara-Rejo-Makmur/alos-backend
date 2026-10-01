# Executive authority and Strategy contracts

Canonical membership provisioning and membership updates grant EXECUTIVE only
navigation.read, work.read, strategy.read, strategy.company.manage,
strategy.review, strategy.approve, and strategy.activate. This role grants neither
Identity administration nor operational domain writes. IT_ADMIN remains a separate
technical role and cannot exercise Executive authority through permission alone.

Company management, review, approval, and activation require both EXECUTIVE and
the action's permission. Strategy scope checks retain active-principal, tenant,
organization, and division workspace restrictions. Authenticated principals still
derive permissions from the active membership; workspace switching and membership
changes do not reuse permissions from another workspace.

All Strategy request bodies use named FastAPI adapters and the configured
CanonicalContractCatalog before converting input to internal domain records.
The dependency-free generated Python TypedDicts describe fields; JSON Schema owns
required fields, nullable values, enums, formats, uniqueness, nested references,
conditional evidence requirements, and additional-property rejection. No schema
fields or validation rules are copied into Backend. Invalid requests return 422;
missing canonical artifacts return 503. Public request schemas reference the same
contracts as canonical OpenAPI.

The audit also closed a missing Strategy read guard and division workspace filter
on assumption collections, and rejects foreign tenant/organization identities in
cascade rules before they can be converted into internal rules.

Stored membership permissions are retained. Existing memberships created with older
defaults can be refreshed through the existing governed membership update operation;
this change does not silently rewrite stored grants or create a migration.

Executive projection contracts do not add an Executive API, aggregation service,
database, migration, or GENESIS/ARA integration. Strategy lifecycle, evidence,
versioning, cascade acceptance, constraints, and audit remain owned by StrategyService.
