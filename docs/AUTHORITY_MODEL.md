# Model Otoritas

ALOS Backend memiliki canonical state, business authorization, ToolExecutor, audit, ReviewPackage reference, keputusan IT, keputusan Director, dan release decision.

GENESIS memiliki AI reasoning dan orchestration, tetapi hasilnya merupakan recommendation atau assurance. `AIRecommendationReference` sengaja tidak memiliki outcome approval. Hanya `AuthoritativeDecision` dari authority `IT` atau `DIRECTOR` yang dapat memiliki outcome `APPROVE`, `RETURN`, `REJECT`, atau `HOLD`.

GIIVEPRO menggunakan tenant, permission, scope, decision, dan release authority ALOS yang sama. Tidak ada jalur authority paralel untuk product layer.

Semua akses ditolak secara default ketika principal, tenant, workspace, permission, atau scope tidak sesuai. Kepercayaan jaringan internal tidak menggantikan authentication dan authorization.

Agent, Capability, dan Skill masuk Backend sebagai definition payload yang telah divalidasi terhadap `alos-contracts`. Pendaftaran selalu menghasilkan versi immutable berstatus `DRAFT`. Aktivasi membutuhkan `DecisionRef` authoritative serta `release_id`; AI recommendation tidak dapat menjalankan transisi approval.

Identity hierarchy mengikat tenant → organization → workspace → membership. Grant role hanya dapat menambah permission dan scope di tenant serta organization yang sama. Record yang hilang, tidak aktif, atau tidak cocok selalu menghasilkan denial.

## Governance dan release

Urutan release authoritative adalah:

```text
IMPLEMENTED
→ AUTOMATED_ASSURANCE
→ AI_REVIEWED
→ READY_FOR_IT
→ IT_APPROVED
→ READY_FOR_DIRECTOR → DIRECTOR_APPROVED (hanya bila material)
→ RELEASED
→ ACTIVE
```

Hasil AI tetap advisory. `AuthoritativeDecision` hanya menerima authority IT atau Director. Release material wajib melewati keduanya; release non-material dapat dilepas setelah IT approval. Kill switch mengubah release aktif menjadi `SUSPENDED`, dan rollback hanya dapat menuju versi subject yang pernah dirilis. Kill switch aktif harus dibersihkan melalui tindakan human authority sebelum rollback.

Automated assurance membandingkan hasil aktual dengan expected behavior. Kategori `NEGATIVE` tidak memiliki jalur auto-pass.

## Runtime dan persistence

`AgentRunAuthority` memvalidasi versi registry ACTIVE, canonical input, Principal,
permission/scope, tool allowlist, correlation dan budget sebelum membuat run. Backend
menyimpan run/step, cancellation, penggunaan yang diketahui, hasil canonical dan audit.
GENESIS menjalankan planning melalui snapshot authority tersebut; snapshot tidak memberi
permission baru. Child run tetap dibuat dan divalidasi Backend dengan authority lebih sempit.

Dokumen dan SourceVersion immutable, klasifikasi, human verification, evidence lineage
dan content hash dimiliki Backend. Retrieval melewati ToolExecutor dan owner service.
Content dan history merupakan data tanpa instruction authority. Chat tidak otomatis
menjadi memory organisasi atau sumber fakta terkini.

SQL repository dan migration menjalankan persistence aplikasi; in-memory repository
dipakai sebagai test double. `source.search_context` dan business readers memerlukan
registrasi, lifecycle, allowlist dan permission yang sesuai. Diagnostic echo hanya
tersedia pada mode pengujian eksplisit dan tidak diaktifkan di production.
