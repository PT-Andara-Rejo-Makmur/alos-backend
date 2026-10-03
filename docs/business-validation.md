# Validasi operasi bisnis ALOS

Validasi lokal pada 3 Oktober 2026, branch `development`, menggunakan source
aktual lima repository dan PostgreSQL 16 disposable. Laporan ini membedakan
pengujian integrasi aktual dari acceptance provider model live.

## Perubahan per repository

| Repository | Perubahan |
| --- | --- |
| alos-contracts | Kontrak proses, aturan perusahaan, paket pengajuan, hubungan bisnis, ringkasan kinerja, binding KPI, upload dokumen, progress ARA, receipt Task, dan permintaan capability; OpenAPI serta keluaran Python/TypeScript diperbarui. |
| alos-backend | Satu proses lintas divisi dengan otorisasi terkini; linkage Sales–Property, Property–Finance–Legal, HR–Identity–GA; lifecycle operasional; KPI SQL; ingestion, queue SQL, notification; Task yang diperiksa manusia; Factory dan release governance existing. |
| genesis-ai | Observer progress publik di adapter HTTP, memakai hook runtime existing dan callback Backend yang terautentikasi. Engine tetap tidak mengambil kewenangan bisnis atau melakukan IO HTTP langsung. |
| alos-web | Antrean tindakan, paket dan riwayat proses, ringkasan kinerja nyata, KPR, hiring manual, upload, hubungan Project, binding Closing, Executive projection, progress ARA, pemeriksaan Task dan formulir kebutuhan capability. Label Strategy/Executive disederhanakan tanpa redesign besar. |
| alos-infra | Worker dari image Backend pada private network; volume object dokumen bersama; validasi isolasi; script proof runtime pada container/database disposable dan panduan operasional. |

## Migration dan kompatibilitas

Enam belas migration additive ditambahkan setelah `0033`, dengan satu head
`0049_contract_review_context`:

| Revision | Fungsi |
| --- | --- |
| 0034 | Proses, langkah, aturan, keputusan dan riwayat bisnis |
| 0035 | Hubungan origin bisnis lintas domain |
| 0036 | Perluasan lifecycle HR, Legal dan generic IT Release |
| 0037 | Source binding KPI dan perhitungan dengan provenance |
| 0038 | Progress publik ARA persisten |
| 0039 | Metadata unggahan dokumen dan ingestion |
| 0040 | Hubungan kesiapan fasilitas onboarding |
| 0041 | Hubungan Payment Certificate dengan progress pekerjaan |
| 0042 | Notification bisnis persisten |
| 0043 | Tujuan, prioritas, penanggung jawab Project dan Task proses |
| 0044 | Lineage transaksi bank pembayaran yang tidak dapat dipakai ulang |
| 0045 | Konteks pembayaran/KPR/akad Sales |
| 0046 | Konteks origin bisnis Legal Contract |
| 0047 | Receipt eksekusi proposal Task ARA |
| 0048 | Permintaan capability berbasis kebutuhan bisnis |
| 0049 | Dokumen pendukung kontrak/sertifikat, kebutuhan Legal dan alasan arahan pimpinan |

Migration tidak mengganti source Employee, Identity, Shared Work, Strategy,
registry atau ledger existing. Validasi PostgreSQL memeriksa upgrade dari
database kosong, head dan metadata aktual. Downgrade yang menghapus lineage
atau audit ditolak; pemulihan memakai aplikasi kompatibel, koreksi additive,
atau snapshot DB/object store yang konsisten.

## Boundary API dan kewenangan

API baru berada di `/api/v1/processes`, `/api/v1/business`,
`/api/v1/business/capability-requests`, upload/status/content `/api/v1/documents`,
progress dan receipt Task `/api/v1/ara`. Command domain mencakup hiring HR,
pelaksanaan Change Order, pembuatan Payable dari Payment Certificate, serta
hubungan pembayaran dengan transaksi bank. Rincian request/response berada di
OpenAPI canonical; panduan alur ada di `business-operations.md`.

Routing memakai tenant, organisasi, workspace, role, permission, fakta bisnis
dan versi aturan. Batas nominal harus berasal dari konfigurasi perusahaan.
Backend memvalidasi membership/account terkini, snapshot pengajuan, pemeriksa
independen dan urutan langkah pada setiap tindakan. Dua membership tetap
memerlukan dua tindakan eksplisit. Paket yang ditugaskan tidak memberi akses
penuh ke workspace pemilik. Arahan Direktur membutuhkan alasan dan role yang
berwenang; acknowledgement tidak menjadi keputusan.

Booking CONFIRMED dan Unit RESERVED tersimpan secara atomic; Closing COMPLETED
menghasilkan Unit SOLD. Certificate tetap menjadi bukti pekerjaan dan
menghasilkan satu Payable; otorisasi independen, pembayaran dan reconciliation
tetap mengikuti resource Finance existing. HR memakai `hr.employees` dan
service Identity existing; offboarding memerlukan pencabutan akses/sesi aktual.
Kontrak kerja mendukung pemeriksaan Legal sesuai kebutuhan dan dokumen yang
disetujui sebelum ACTIVE. Generic IT Release memerlukan CI Run, bukti
penerapan/verifikasi dan hasil kegagalan/rollback yang tercatat.

Recruitment mencatat divisi pemohon, posisi, jumlah kebutuhan dan alasan.
Pengajuan dengan divisi pemohon wajib memiliki aturan dan pemeriksaan yang
selesai sebelum hiring: kepala divisi pemohon → kepala HR → Finance jika
diwajibkan → Direktur sesuai aturan. Backend menentukan workspace pemeriksa
dari kebutuhan yang tersimpan dan menolak digest atau aturan yang berubah.
Paket terbatas dapat dibaca pemeriksa tanpa membuka akses langsung ke catatan
HR. Web menampilkan fakta kebutuhan dan mengirim command tanpa memilih actor.
Perluasan RECRUITMENT menggunakan tabel proses existing tanpa migration baru.

## Kinerja, dokumen dan intelligence

Enam domain memiliki summary SQL. Sales mencakup lead, peluang, Booking,
Closing dan nilai penjualan tercatat; Finance mencakup piutang/utang dan jatuh
tempo serta reconciliation; Property mencakup NCR dan pengajuan; HR mencakup
headcount, recruitment, onboarding, cuti dan penilaian; Legal mencakup review,
risiko dan kasus; IT mencakup sistem, insiden, temuan keamanan dan release.
Sumber yang belum memadai tetap `null` dengan `available=false`.

Binding otomatis yang dibuktikan adalah COUNT Closing selesai dan SUM nilai
Closing dalam IDR, pada versi target dan periode divisi Sales. ACTUAL menyimpan
provenance dan menunggu verification existing. Executive memakai summary dan
Strategy aktual serta membedakan keputusan, perhatian dan informasi.

Ingestion mendukung UTF-8 TEXT dan DOCX, dengan batas ukuran, classification,
scope, hash/object immutable, Source/SourceVersion/DocumentVersion dan lease
fencing. Approval dokumen tetap wajib sebelum konten dibaca ARA. Queue SQL
memakai claim eksklusif, retry terbatas, pemulihan lease dan notification
berdasarkan aturan tenggat; pemberitahuan tidak menambah kewenangan.

Progress ARA berasal dari runtime aktual dan tidak memuat reasoning tersembunyi.
Proposal TASK tetap NEEDS_REVIEW dan `executed=false`; command manusia
memvalidasi kewenangan terkini, membuat satu Task dan menyimpan receipt terpisah.
Permintaan capability memakai Factory existing untuk REUSE/CREATE, registry
persisten, AI review advisory, pengujian, keputusan IT dan release existing.

## Hasil pengujian

| Repository | Hasil |
| --- | --- |
| alos-contracts | 159 schema valid; OpenAPI, examples, generation Python/TypeScript dan compatibility terhadap HEAD awal lulus; 234 tes lulus. |
| alos-backend | Ruff seluruh repository lulus; Mypy 241 file lulus; satu migration head; full pytest **1.358 lulus**, tanpa failure/error/skip, 1.305,31 detik. |
| genesis-ai | Ruff seluruh repository lulus; Mypy 177 file dan import aplikasi lulus; full pytest **597 lulus**, tanpa failure/error/skip, 70,78 detik. |
| alos-web | ESLint tanpa warning dan typecheck lulus; full Vitest **524 lulus**, tanpa failure; build produksi Next.js lulus. |
| alos-infra | Enam konfigurasi Compose, 27 invariant topology, syntax Python/PowerShell/Bash dan Caddy lulus; restore proof PostgreSQL/pgvector serta penolakan checksum tidak cocok lulus. Worker image berhasil memproses TEXT/DOCX, mempertahankan hash/versi, menolak konten sebelum approval dan berhenti dengan exit code 0 setelah SIGTERM. |

Worker proof memakai private network PostgreSQL dan CLI Linux Docker melalui
WSL karena proxy CLI Windows serta jalur `host.docker.internal` pada host lokal
tidak merespons. Verifikasi start/stop membaca state container aktual. Tidak
ada restart/reset stack aplikasi. Container/database proof telah dibersihkan.

Pada validasi Web terakhir, pool fork Vitest mengalami ENOENT saat membaca
file transform sementara di Windows. Test yang terkait lulus sendiri dan
regresi 22 test lulus memakai `--pool=threads --maxWorkers=1`; seluruh 524
test kemudian lulus memakai `--pool=threads --maxWorkers=4`. Tidak ada
perubahan dependency atau source test runner untuk mengatasi kendala lokal ini.

Setelah validasi repository, acceptance berikut dijalankan ulang berurutan
pada source commit yang diekspor, melalui API/layanan Backend aktual,
database yang dimigrasi dan aplikasi GENESIS aktual melalui ASGI:
**19 tes lulus, 170,86 detik**, tanpa failure/error/skip. Delapan tes integrasi
meliputi tujuh rangkaian A–H (D dan E berada dalam satu tes berkesinambungan)
serta satu tes recruitment tambahan. Sebelas tes unit memeriksa kewenangan
provider ARA. Kasus recruitment tambahan membuktikan routing divisi pemohon,
penolakan workspace/tenant/organisasi asing, perubahan kebutuhan yang
membatalkan pemeriksaan lama, pengajuan ulang dengan riwayat tetap tersimpan,
dan keterlibatan Finance/Direktur sesuai aturan perusahaan.

| Skenario | Bukti |
| --- | --- |
| A | Lead → Qualified → Opportunity → Booking → reservasi Unit → KPR/akad → Closing; status Unit konsisten. |
| B | Change Order → teknis → Finance → Legal → keputusan sesuai aturan termasuk Direktur → implementation. |
| C | Progress terverifikasi → Certificate → Finance → satu Payable → keputusan independen → Payment → Bank Transaction → Reconciliation; origin dua arah. |
| D | Kebutuhan recruitment → pemeriksaan divisi pemohon dan HR → candidate/interview → keputusan hiring → Employee existing → onboarding → akun aktif → GA dan divisi siap. Hiring sebelum aturan/pemeriksaan ditolak. |
| E | Handover → asset return → Finance sesuai aturan → Identity revoke → sesi/akses ditolak → Employee INACTIVE. |
| F | Strategy/Target → Closing aktual → source binding → ACTUAL → verification → Executive Performance. |
| G | Pertanyaan → akses → sumber canonical → progress → jawaban/bukti → proposal Task → pemeriksaan manusia; replay idempotent, perubahan command ditolak dan pencabutan permission berlaku. Paket proses lintas divisi terbaca tanpa akses langsung ke record pemilik. |
| H | Kebutuhan → Factory CREATE → draft → AI review dan hasil HTTP yang benar-benar diamati → keputusan IT → release → Agent ACTIVE; kebutuhan berikutnya REUSE katalog aktual. |

Log mesin lokal berada di `.codex/validation` pada workspace:
`contracts-final-results.xml`, `backend-final-results.xml`,
`genesis-results.xml`, `web-final-results.json`, `web-final-build.log`,
`worker-final-results.log` dan `recruitment-committed-acceptance-results.xml`.
Hasil penuh di atas berasal dari working tree yang juga memuat perubahan lokal sebelumnya;
perubahan tersebut dipertahankan dan tidak digabung ke commit tugas ini.

Revision source yang dibuktikan:

| Repository | Commit source |
| --- | --- |
| alos-contracts | `6e9e3e78afd95bcc4e4d6bb3bbf25618153d925a` |
| alos-backend | `358ad939de50ef78dbfc0e851446aec71ea4b6fb` |
| genesis-ai | `82c11be04e38604fef966bf775569c8db48ecd43` |
| alos-web | `1a383ce469146350ffb0e81a90863d2f2ecb95b4` |
| alos-infra | `718a4340ec8989ae13bdc6e9a527043242a70c78` |

Source commit Contracts/Backend/GENESIS/Web diekspor lagi ke
`committed-recruitment-sources`, dengan manifest
`recruitment-source-revisions.json`. Salinan tersebut lulus Ruff/Mypy Backend
serta 19 test acceptance/authority dan 22 test Web recruitment/HR tanpa
mengikutsertakan perubahan ARA lokal.

## Batas yang masih berlaku

- Tidak ada klaim acceptance 9Router/provider model live. Runtime deterministik
  dan transport pengujian membuktikan boundary aplikasi, bukan akses provider
  production. Integrasi gateway/provider existing tetap dipakai.
- PDF, OCR dan format dokumen selain TEXT/DOCX belum didukung ingestion ini.
- KPI otomatis terbatas pada Closing COUNT/SUM. Posisi kas, pemakaian anggaran,
  persentase konstruksi, kontrak mendekati akhir dan persentase kesiapan restore
  belum mempunyai projection yang memadai dan tetap unavailable.
- Eksekusi proposal ARA yang diperiksa manusia tersedia untuk TASK. Proposal
  material/capability/research tetap mengikuti jalur canonical masing-masing.
- Persiapan implementasi/assurance teknis Agent memakai service governance IT
  existing; formulir kebutuhan bisnis tidak menjadi editor teknis release.
- Aturan perusahaan, routing dan batas kewenangan perlu dikonfigurasi dari
  keputusan perusahaan. Tidak ada nominal perusahaan yang dikarang.
- Deployment production, push remote dan pemulihan object volume production
  tidak dilakukan dalam pekerjaan lokal ini.
