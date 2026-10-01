# Audit business domains development — 1 Oktober 2026

Audit dilakukan sebelum perubahan kode. Fetch kelima remote development cocok dengan
baseline yang diberikan, dan seluruh working tree bersih. Tidak ada AGENTS.md pada
repository. GENESIS tidak akan dimodifikasi atau dihubungkan ke domain baru.

## Struktur aktual

- Contracts belum memiliki sales/marketing/property/finance schema. Generator existing
  menggunakan JSON Schema untuk Python TypedDict dan TypeScript; public OpenAPI 3.1,
  compatibility check, schema/reference validation, serta examples/full tests tersedia.
- Backend dedicated domain packages sales/property/finance masih kosong. Generic
  DomainCrudService menyediakan scope dan FK validation, tetapi menerima status string
  dan tidak menjalankan domain lifecycle. Audit generic route dilakukan setelah commit,
  sehingga tidak dipakai sebagai transactional boundary domain canonical baru.
- SqlAuditRepository.append_in_session sudah tersedia untuk transaksi mutation/audit
  atomic. Strategy sudah memakai pola transaksi atomic; fondasi itu dipertahankan.
- Shared Work Project authoritative memakai project_workspaces; Approval hanya mendukung
  PROJECT/TASK. Tidak ada business approval subject untuk booking, closing, change order,
  atau payment certificate. Keputusan material tanpa authority ini tetap ditolak.
- ExecutiveProjectionService sudah membaca Strategy/Shared Work; tiga koneksi bisnis
  baru akan ditambahkan melalui dedicated service, dengan Legal/HR/IT tetap unavailable.
- Permission existing: sales.*, marketing.*, property.*, finance.*; active principal
  dan workspace tetap required. IT_ADMIN/EXECUTIVE tidak memperoleh business write baru.

## Persistence aktual

0017_sales_marketing: 11 tabel sales (customers, leads, opportunities, site_visits,
bookings, closings, customer_followups, customer_complaints, pricings, pricing_items,
collaterals) dan 4 marketing (campaigns, channels, attributions, contents).

0018_property: property_units, project_milestones, construction_packages,
construction_updates, quality_inspections, quality_ncrs, safety_incidents,
change_orders, payment_certificates, project_handovers, land_pipeline.
Project foreign key mengarah core.projects, bukan Property Project baru.

0014_finance: bank_accounts, bank_transactions, receivables, receivable_payments,
payables, payable_payments, budgets, budget_lines, reconciliations,
reconciliation_items, tax_obligations, tax_documents, month_closes, month_close_items.

Ketiganya menggunakan tenant_id/organization_id/workspace_id dan created_at/updated_at.
Amounts Numeric; tidak ada opening bank balance. Receivable/payable/payment/budget line
tidak memiliki currency. Tidak ada official lifecycle enum/check constraint selain
default string status. Migration lama tidak akan diedit.

## Menu dan form aktual

| Workspace | Menu existing | Sumber yang dapat dihubungkan |
| --- | --- | --- |
| Sales | Summary | Dedicated Sales/Marketing counts dan source status; tidak ada revenue/conversion asumsi |
| Sales | Pipeline | Opportunities; tahap UI existing dipertahankan sebagai internal pipeline, bukan authority KPR/legal |
| Sales | Leads | Customers dan Leads; field notes pada lead tidak tersedia dan tidak akan disimpan pura-pura |
| Sales | Activities | Site Visits, Customer Followups/Complaints |
| Sales | Bookings | Booking request/Closing internal records; final financial/legal authority tetap unavailable |
| Sales | KPR | Tidak ada tabel KPR; tetap unavailable |
| Sales | Campaigns | Campaign/Channel/Attribution/Content, Pricing/Collateral actual records |
| Sales | Performance | Strategy target/detail projection; attribution/CPL/official closing metric belum tersedia |
| Property | Summary | Dedicated Property counts; progress hanya input construction_updates |
| Property | Portfolio | Existing Shared Work Projects; Land Pipeline actual records |
| Property | Units | property_units; tidak ada field technical readiness/inspection readiness terpisah |
| Property | Progress | construction_updates dan milestone dates; tidak ada planned curve untuk S-Curve |
| Property | Execution | Package/Milestone/Update/Change Order/Payment Certificate/Handover |
| Property | Contractors | Tidak ada contractor registry; contractor_name hanya field package, registry tetap unavailable |
| Property | Materials | Tidak ada material/procurement source; tetap unavailable |
| Property | Quality | Inspections, NCRs, Safety incidents; severity wajib explicit, tidak diinfer |
| Property | Budget | Tidak ada RAB/BOQ persistence; tetap unavailable |
| Property | Performance | Strategy projection; progress evidence tidak menjadi business performance kedua |
| Finance | Summary | Dedicated counts/status; saldo/likuiditas authoritative belum tersedia |
| Finance | Liquidity | Accounts/Transactions manual; recorded cash movement dipisahkan dari bank balance |
| Finance | Receivables | Receivables dan transactional payment history/outstanding |
| Finance | Payables | Payables dan transactional payment history/outstanding; tidak ada transfer bank |
| Finance | Budget | Budget/Lines; actual/commitment/forecast mapping belum tersedia |
| Finance | Reconciliation | Reconciliation/Items; Month Close/Items internal checklist |
| Finance | Tax | Internal obligations/documents; pembayaran/pelaporan DJP tetap unavailable |
| Finance | Performance | Existing Strategy target/detail projection; no fake cash KPI |

Form existing mengandung banyak field tanpa kolom: source document/evidence, owner tertentu,
KPR/SPK, technical readiness, RAB/BOQ, refund, tax base, invoice currency, budget allocation
lintas Strategy. Field unsupported tidak diaktifkan pada canonical form, dan capability
tersebut tetap unavailable. Tidak ada silent data loss.

## Keputusan implementasi

User mengonfirmasi belum ada policy lifecycle resmi; gunakan aturan konservatif dari
schema/UI existing. Default persisted status dipertahankan. Transisi internal eksplisit,
terminal history immutable, material approval tanpa canonical authority ditolak.

Sales membaca Unit melalui port milik Property. Visibility hanya unit dalam scope aktif
atau unit yang project-nya memang dibagikan melalui canonical Shared Work ke workspace
Sales aktif, dengan tenant/org yang sama. Tidak ada write atau copy Property Unit.

Finance memakai exact decimal strings pada wire dan Decimal saat persistence/calculation.
Outstanding berubah hanya melalui payment transaction dengan row lock; overpayment,
duplicate nonempty payment reference, dan mutation history ditolak. Tidak ada sum lintas
currency yang tidak tercatat. Reconciliation selesai hanya setelah seluruh item cocok;
month close mendukung persiapan/checklist internal; final close belum tersedia.
Existing historical CLOSED tetap mengunci period.

Tidak perlu tabel baru untuk implementation foundation ini. Jika constraint baru akhirnya
dibutuhkan, hanya append-only migration dengan alasan dan proof; tidak ada asumsi migration.

## HEAD yang diaudit

| Repository | Development HEAD |
| --- | --- |
| alos-contracts | `8b6f56a346c9d2eb2924e4045d00fe3148784ac4` |
| alos-backend | `c7f9a04304cef097f44feaf92012a7d7b3ad51cd` |
| alos-web | `74bd813e16c38ead61bbeed05a1a3f3f44ac46b3` |
| alos-infra | `43640bd311cb640f815f371a77a0be645c88a756` |
| genesis-ai | `1ffb055a82cc04d668a7582c0a38af03614fcb57` |

## Implementasi canonical

Empat namespace API: `/api/v1/sales`, `/api/v1/marketing`, `/api/v1/property`,
`/api/v1/finance`. Semua memiliki `/overview`, fixed collection/detail routes,
POST create, PATCH hanya field editable, dan POST `/{identity}/transition` jika
lifecycle mendukungnya. Tidak ada DELETE historis. Generic mutation ke empat schema
mengembalikan 409 CANONICAL_DOMAIN_MUTATION_REQUIRED, generic read tetap compatibility.
Sales menyediakan `/property-units` sebagai narrow read port dan
`/opportunities/{identity}/pipeline` dengan allowed actions milik Backend.

Scope berasal active Principal + persisted active workspace. IT_ADMIN/EXECUTIVE
bukan writer. Predicate transition_authorized dijalankan oleh repository sebelum
business rule, write, dan audit, bukan hanya untuk projection allowed_transitions.
Penolakan predicate authority 403 BUSINESS_TRANSITION_DENIED, konflik lifecycle
409, reference tak visible 404. Division Lead masih diperlukan untuk internal
Reconciliation/Tax closure yang telah tersedia. Pricing activation, Budget final
approval/activation/close, dan Month Close final belum memiliki canonical authority
dan tidak mempunyai allowed transition, termasuk untuk Division Lead.
Legacy status tetap tampil sebagai recorded status; status tak dikenal tidak dapat
mutasi atau menjadi reference command. Tidak ada remapping migration lama.

Read collection/overview memakai repeatable-read read-only snapshot. Count 0 hanya
berasal successful canonical read; unavailable/error tidak pernah menjadi 0.
Overview memuat exact record counts, source status, authoritative flag, timestamp
terakhir yang diketahui. Tidak ada bank balance, revenue, conversion, CPL,
planned progress curve atau budget variance dari asumsi.
Executive membaca owner overview ports dengan COMPANY visibility hanya jika
DataScope.COMPANY. SALES mencakup Sales dan Marketing, PROPERTY/FINANCE masing-masing
owner service. Retrieval failure menghasilkan source ERROR terpisah; contract/security
failure fail closed. Legal/HR/IT tetap UNAVAILABLE. Tidak ada tabel Executive baru.

Mutations memakai satu PostgreSQL transaction dengan audit.append_in_session dan
canonical projection validation sebelum commit. Payment row lock mempertahankan
exact outstanding; audit parent dan payment history atomic. Period advisory locks
diambil sebelum record locks, dalam urutan deterministik, untuk serialisasi close
versus writes. Recorded financial periods immutable. Receivable/payable outstanding
boleh berubah lewat payment event periode terbuka; invoice original tetap immutable.
Budget tidak dipetakan ke actual/commitment tanpa sumber. Reconciliation harus seluruh
item MATCHED dengan transaction/account/period/amount yang sesuai. Month Close boleh
dibuat OPEN dan checklist boleh COMPLETED; OPEN → CLOSED tetap unavailable.
Historical CLOSED tetap readable dan tidak dapat dibuka kembali atau diubah.
Tax closure internal perlu amount, due date, dan canonical documents RECORDED;
tidak merepresentasikan submit/pembayaran ke DJP.

No migration, table, connector, AI integration, atau business approval system baru.
`tests/integration/test_business_domains.py` menggunakan migrated PostgreSQL, scoped
HTTP sessions, exact Decimal, concurrent payment proof, rollback setelah audit insert,
partial source failure, unknown lifecycle dan canonical contract failure.

## Lifecycle command yang tersedia

| Domain / resource | Initial | Allowed transitions |
| --- | --- | --- |
| sales/customers | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE |
| sales/leads | NEW | NEW → FOLLOW_UP, QUALIFIED, INACTIVE; FOLLOW_UP → QUALIFIED, INACTIVE; QUALIFIED → INACTIVE |
| sales/opportunities | OPEN | OPEN → LOST, CANCELLED |
| sales/site-visits | SCHEDULED | SCHEDULED → COMPLETED, CANCELLED |
| sales/bookings | PENDING | PENDING → CANCELLED |
| sales/closings | OPEN | OPEN → CANCELLED |
| sales/customer-followups | OPEN | OPEN → COMPLETED, CANCELLED |
| sales/customer-complaints | OPEN | OPEN → IN_PROGRESS, CLOSED; IN_PROGRESS → CLOSED |
| sales/pricings | DRAFT | No activation; historical ACTIVE → INACTIVE remains available |
| sales/pricing-items | explicit input / no status | no status command |
| sales/collaterals | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE |
| marketing/campaigns | PLANNED | PLANNED → ACTIVE, CANCELLED; ACTIVE → COMPLETED, CANCELLED |
| marketing/channels | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE |
| marketing/attributions | explicit input / no status | immutable history |
| marketing/contents | DRAFT | DRAFT → PUBLISHED; PUBLISHED → ARCHIVED |
| property/property-units | AVAILABLE | AVAILABLE → ON_HOLD; ON_HOLD → AVAILABLE |
| property/project-milestones | OPEN | OPEN → COMPLETED, CANCELLED |
| property/construction-packages | PLANNED | PLANNED → IN_PROGRESS, CANCELLED; IN_PROGRESS → ON_HOLD, COMPLETED, CANCELLED; ON_HOLD → IN_PROGRESS, CANCELLED |
| property/construction-updates | RECORDED | immutable history |
| property/quality-inspections | explicit input / no status | immutable history |
| property/quality-ncrs | OPEN | OPEN → IN_PROGRESS; IN_PROGRESS → CLOSED |
| property/safety-incidents | OPEN | OPEN → IN_PROGRESS; IN_PROGRESS → CLOSED |
| property/change-orders | DRAFT | DRAFT → SUBMITTED, CANCELLED; SUBMITTED → CANCELLED |
| property/payment-certificates | DRAFT | DRAFT → SUBMITTED, CANCELLED; SUBMITTED → CANCELLED |
| property/project-handovers | PLANNED | PLANNED → COMPLETED, CANCELLED |
| property/land-pipeline | OPEN | OPEN → ON_HOLD, CLOSED; ON_HOLD → OPEN, CLOSED |
| finance/bank-accounts | ACTIVE | ACTIVE → INACTIVE; INACTIVE → ACTIVE |
| finance/bank-transactions | POSTED | immutable history |
| finance/receivables | OPEN | OPEN → CANCELLED |
| finance/receivable-payments | POSTED | immutable history |
| finance/payables | OPEN | OPEN → CANCELLED |
| finance/payable-payments | POSTED | immutable history |
| finance/budgets | DRAFT | DRAFT → UNDER_REVIEW; UNDER_REVIEW → DRAFT; historical APPROVED/ACTIVE/CLOSED readable and immutable |
| finance/budget-lines | explicit input / no status | no status command |
| finance/reconciliations | OPEN | OPEN → CLOSED |
| finance/reconciliation-items | UNMATCHED | UNMATCHED → MATCHED |
| finance/tax-obligations | OPEN | OPEN → CLOSED |
| finance/tax-documents | DRAFT | DRAFT → RECORDED |
| finance/month-closes | OPEN | No final close; historical CLOSED readable and period remains protected |
| finance/month-close-items | OPEN | OPEN → COMPLETED |

Final Booking CONFIRMED, Closing COMPLETED, Opportunity WON, Unit RESERVED/SOLD,
Change Order/Payment Certificate APPROVED tidak diberikan tanpa authority lintas
business canonical. Existing stored terminal values tetap dapat dibaca. Internal
pipeline hanya Lead → Qualified → Survey → Booking; bukan progress KPR/SPK/legal.

Audit governance 2 Oktober 2026 memakai development aktual, bukan baseline audit
awal di atas. Tidak ada perubahan schema/migration/status enum: allowed_transitions
adalah capability subset dari canonical enum dan dapat kosong. Web merender array
dari Backend; tidak ada tombol material decision yang dihitung dari role sendiri.
PostgreSQL HTTP regression mencakup member/lead, historical records, server-side
predicate, rollback audit, dan scope isolation.
