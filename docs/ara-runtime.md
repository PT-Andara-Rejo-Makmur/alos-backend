# Authority Percakapan ARA

Public `/api/v1/ara` menerima intent pengguna dan membutuhkan Principal aktif.
Thread, message dan run dipersist PostgreSQL, dibatasi tenant, organization,
workspace dan actor. Lookup memeriksa parent thread; objek tidak terlihat memberi
404. Reservasi run aktif mencegah dua message paralel pada thread yang sama.
History API dibatasi 500 pesan secara kronologis; refresh tidak menghapus percakapan.

## Context dan mode

Backend memilih definition released ACTIVE sebelum mereservasi percakapan,
membatasi katalog tool terhadap Principal, permission, scope dan classification.
`restricted.access` diperlukan untuk classification RESTRICTED; nama role saja
tidak memberikannya. ToolExecutor memeriksa kembali identity, division, project,
registry, lifecycle, kill switch dan budget pada setiap panggilan.

NORMAL menggunakan katalog dinamis dan model melalui GENESIS/ModelGateway.
TEST menggunakan matcher/deterministic route hanya pada non-production dengan
`ENABLE_TEST_TOOLS` di Backend dan `ENABLE_TEST_RUNTIME` di GENESIS. Local Compose
mematikan keduanya secara default. Tidak ada fallback dari NORMAL ke TEST.

Batas ARA maksimal 16 steps, 12 tool calls, 12.000 tokens dan deadline 30 detik;
definition/Principal hanya dapat mempersempit batas tersebut. Model cost yang
belum diketahui tidak dinyatakan nol. Backend mengatur runtime transport sesuai
deadline run dan overhead terbatas. Gagal/timeout dipersist dan diproyeksikan 503.

Enam pesan terakhir, masing-masing maksimum 1.000 karakter, diteruskan sebagai
data tanpa instruction authority. Follow-up dapat memilih source lama, tetapi
fakta harus dibaca dan diverifikasi lagi. History bukan memory organisasi otomatis.

## Evidence, proposal dan child

Read adapter memanggil owner service dan mendaftarkan canonical evidence dengan
version, content hash, waktu, scope, run dan correlation. GENESIS memverifikasi
evidence/claim sebelum Backend memproyeksikan jawaban. Unknown tetap null; failed
source tidak menjadi data kosong palsu. CONVERSATION tidak menyatakan sumber.

TASK/material/capability menghasilkan proposal yang memerlukan review. ARA
tidak mengambil keputusan IT/Director, membuat release, atau mengaktifkan Agent.
Eksekusi TASK yang sudah diperiksa berada pada command Backend terpisah.

Research memerlukan permission yang sesuai dan evidence terkini. Backend dapat
membuat child business reader depth 1, satu tool, 1.000 tokens dan deadline 10
detik dengan authority lebih sempit. Child memakai sumber yang dibaca parent;
run authority menegakkan lineage dan cancellation. Cancellation sebelum selesai
mengalahkan completed result yang terlambat.

Regresi ada pada tests PostgreSQL conversation/authority/memory dan script Infra
`verify-ara-roundtrip.py` serta `ara-smoke.py`. TEST dan provider mock membuktikan
protokol/grounding, bukan kualitas model nyata. Lihat
[readiness terkini](https://github.com/PT-Andara-Rejo-Makmur/alos-infra/blob/development/docs/PRODUCTION_READINESS_2026-10-04.md).
