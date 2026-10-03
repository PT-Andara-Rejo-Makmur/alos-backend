# Operasi bisnis dan kewenangan

Backend menyimpan status bisnis, routing perusahaan, keputusan, pelaksanaan,
bukti, dan audit. GENESIS memberi analisis dan rancangan; ARA memberi jawaban
dan proposal yang harus diperiksa manusia. Kewenangan tidak berasal dari teks
pengguna maupun keluaran model.

## Pemeriksaan dan keputusan

`POST /api/v1/processes/policies` mengonfigurasi aturan per perusahaan, ruang
kerja pemilik, dan jenis pengajuan. Endpoint membutuhkan IT_ADMIN dengan
`it.write` serta membership aktif. Routing menunjuk ruang kerja dan role,
bukan nama pemeriksa. Semua ruang kerja tujuan harus aktif dalam perusahaan
yang sama. Perubahan aturan menaikkan versi dan membuat pemeriksaan lama
tidak dapat dipakai untuk keputusan baru.

Jenis pengajuan: CHANGE_ORDER, PAYMENT_CERTIFICATE, BOOKING, ONBOARDING,
OFFBOARDING, EMPLOYMENT_CONTRACT, dan CAPABILITY_REQUEST. Status proses
terpisah dari status record domain. Langkah dapat berupa REVIEW, DECISION,
EXECUTION, atau ACKNOWLEDGEMENT. Shared Work Approval tetap menjadi keputusan
action-scoped untuk tindakan material yang sudah menggunakan fondasi tersebut.

Aturan mendukung `executive_required`, `executive_amount_limit`,
`executive_acknowledgement`, `independent_steps`, kebutuhan Legal/settlement,
serta konfigurasi tenggat, pengingat, dan tujuan eskalasi. Tidak ada batas
nominal perusahaan bawaan. Gunakan nilai yang telah ditetapkan perusahaan.
Finance terlibat dalam Change Order bila biaya berubah; Legal terlibat bila
kontrak berubah. Direktur terlibat sesuai aturan atau permintaan beralasan
dari kepala divisi pemilik melalui `request-direction`.

Setiap tindakan memvalidasi ulang membership, actor/account aktif, role,
permission, versi aturan, dan digest catatan pendukung. Pemeriksa independen
tidak boleh menjadi pembuat atau pemeriksa sebelumnya. Satu account yang
memiliki dua membership tetap harus bertindak dua kali dalam ruang kerja
masing-masing bila aturan mengizinkannya. Audit mencatat actor, ruang kerja,
role, permission, langkah, alasan, bukti, waktu, dan correlation ID.

Finance dan Legal menerima paket pengajuan terbatas. Penugasan proses tidak
memberi akses menyeluruh ke record ruang kerja pemilik. Finance hanya dapat
membuat satu Payable dari sertifikat yang sudah disetujui dan pemeriksaan yang
masih sesuai. Hubungan origin tersimpan dua arah. Legal dapat menghubungkan
kontraknya ke pengajuan yang ditugaskan; perubahan paket mengembalikan proses
agar pemilik mengajukan pemeriksaan ulang. Riwayat pemeriksaan tetap disimpan.

## Alur domain

- Booking dikonfirmasi dan Unit menjadi RESERVED dalam transaksi yang sama.
  Closing selesai mengubah Unit menjadi SOLD. Konteks KPR mencatat bank,
  dokumen, SP3K, akad, tanggung jawab, dan tindakan berikutnya; akad harus
  selesai sebelum Closing KPR.
- Change Order diperiksa, diputuskan melalui Approval existing, lalu
  pelaksanaan dicatat dengan alasan. Pengajuan yang sudah diputuskan tidak
  boleh ditulis ulang.
- Payment Certificate mengacu pada kemajuan pekerjaan aktual. Setelah
  keputusan pemilik, Finance membuat Payable secara idempotent. Otorisasi
  independen wajib mendahului pembayaran dari origin sertifikat. Hubungan
  transaksi bank harus cocok arah, nilai, dan tanggal, serta tidak boleh
  digunakan kembali untuk pembayaran lain. Reconciliation tetap memakai
  resource Finance existing.
- Hiring membutuhkan recruitment aktif, hasil wawancara selesai, dan
  keputusan HR. Employee dibuat melalui `hr.employees`; tidak ada sumber
  employee kedua. Onboarding memeriksa akses Identity, kesiapan GA, tugas
  terkait, dan divisi. Offboarding memeriksa serah terima, pengembalian aset,
  settlement bila diwajibkan, dan pencabutan akses/sesi aktual sebelum
  Employee menjadi INACTIVE.
- Kontrak kerja dapat meminta pemeriksaan Legal. Keputusan bisnis tetap di
  HR. Aktivasi membutuhkan dokumen yang telah disetujui. Generic IT Release
  memakai CI Run dan hasil penerapan/verifikasi yang dicatat; alur tersebut
  terpisah dari release Agent GENESIS.
- Project menyimpan tujuan, prioritas, dan penanggung jawab yang membership-nya
  valid. Catatan bisnis terkait dibaca dari FK Project existing, dengan izin
  masing-masing domain dalam ruang kerja aktif.

## Kinerja dan ARA

Ringkasan domain dihitung di SQL. Nilai yang belum memiliki sumber tersedia
dikembalikan sebagai null dengan `available=false`. KPI otomatis yang tersedia
adalah jumlah Closing selesai (COUNT) dan nilai Closing selesai (IDR), untuk
target divisi Sales. Binding terikat versi target dan periode. Perhitungan
mencatat provenance immutable dan ACTUAL yang menunggu verification existing;
perhitungan tidak menyatakan dirinya terverifikasi.

ARA membaca tools existing dan paket proses yang dapat diakses pengguna.
Progress berasal dari callback runtime GENESIS, disimpan sebagai event publik,
dan tidak berisi reasoning tersembunyi, prompt, atau log model mentah. Proposal
TASK tetap NEEDS_REVIEW dengan `executed=false`; keputusan manusia membuat
canonical Task melalui Backend dan receipt eksekusi terpisah. Pengulangan
command yang sama tidak membuat Task kedua. Jenis proposal lain tetap harus
melalui jalur kewenangan canonical masing-masing.

Permintaan capability memuat kebutuhan, tujuan, dan konteks opsional. Kepala
divisi memvalidasi kebutuhan bila policy dikonfigurasi. Factory menerima
katalog ACTIVE yang berizin. REUSE harus cocok dengan referensi katalog;
CREATE menghasilkan draft registry persisten. Review memakai endpoint GENESIS
existing. Implementasi, automated assurance, keputusan IT, keputusan Direktur
bila material, release, aktivasi, suspend, kill, dan rollback memakai layanan
governance existing. Proposal maupun AI review tidak merupakan keputusan IT.
Persiapan teknis release memakai service existing dan bukti pengujian aktual;
form kebutuhan bisnis tidak menerima klaim PASS dari pengguna atau model.

## Dokumen dan pekerjaan terjadwal

Unggahan raw dibatasi ukuran, nama file, format, classification, dan scope.
Ekstraksi mendukung UTF-8 TEXT dan DOCX; format lain ditolak secara eksplisit.
Hash dan versi immutable mengacu ke object store privat. Worker menyimpan
Source, SourceVersion, dan DocumentVersion dalam satu transaksi yang dilindungi
lease. Dokumen harus melalui review dan approval sebelum kontennya menjadi
sumber baca ARA. Konten dokumen tidak pernah menjadi instruksi kewenangan.

Jalankan `python -m alos.jobs.runner` menggunakan konfigurasi DB dan object
store yang sama dengan Backend. Worker memakai claim SQL SKIP LOCKED, attempt
fencing, retry terbatas, serta pemulihan lease yang terputus. Pengingat memakai
tenggat dan tujuan eskalasi dari policy, memvalidasi penugasan terkini, dan
mengirim pemberitahuan in-app. Eskalasi tidak mengubah keputusan secara otomatis.

Migration 0034–0049 menambah tabel/kolom/index tanpa mengganti sumber existing.
Jalankan `alembic upgrade head` sebelum aplikasi dan worker. Downgrade yang
berpotensi menghapus audit/lineage ditolak. Pemulihan deployment memakai versi
aplikasi yang kompatibel dan koreksi additive; pemulihan snapshot DB memerlukan
snapshot object store yang sesuai. Jangan menghapus riwayat demi rollback.

## Bukti pengujian

Acceptance A–H berada di integration tests Sales financing, Change Order
contract, certificate payment, HR business flow, business metrics, ARA business
flow, dan capability business flow. Semua menggunakan PostgreSQL yang dimigrasi.
ARA dan Factory/review memakai aplikasi GENESIS aktual melalui transport ASGI.
Automated assurance H membandingkan hasil HTTP yang diamati, bukan nilai PASS
yang ditulis tanpa pengujian. Runtime model deterministik dalam pengujian ini
tidak merupakan bukti acceptance provider 9Router live.

Jalankan lint, typecheck, schema/generation/compatibility, full tests, Web build,
dan validasi Infra sesuai CI repository. Pengujian DB memakai database khusus
melalui `ALOS_TEST_DATABASE_URL`; fixture yang memakai nama DB sama harus
dijalankan berurutan.
