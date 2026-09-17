# Panduan Kontribusi

1. Pertahankan Backend sebagai source of truth dan boundary bisnis authoritative.
2. Gunakan contract dari `alos-contracts`; jangan membuat schema lintas repository versi lokal.
3. Tambahkan migration baru dan jangan mengubah migration yang sudah dirilis.
4. Tambahkan pengujian positif, negatif, authorization, dan tenant isolation sesuai risiko perubahan.
5. Jalankan `ruff check .`, `mypy`, dan `pytest` sebelum membuka pull request.
6. Jangan menambahkan secret, credential provider, data produksi, atau koneksi database untuk Agent.
7. Jangan menambahkan service/framework di luar baseline tanpa Architecture Decision Record yang disetujui.

AI review merupakan assurance tambahan dan tidak menggantikan approval IT atau Director.
