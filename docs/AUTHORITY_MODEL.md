# Model Otoritas

ALOS Backend memiliki canonical state, business authorization, ToolExecutor, audit, ReviewPackage reference, keputusan IT, keputusan Director, dan release decision.

GENESIS memiliki AI reasoning dan orchestration, tetapi hasilnya merupakan recommendation atau assurance. `AIRecommendationReference` sengaja tidak memiliki outcome approval. Hanya `AuthoritativeDecision` dari authority `IT` atau `DIRECTOR` yang dapat memiliki outcome `APPROVE`, `RETURN`, `REJECT`, atau `HOLD`.

GIIVEPRO menggunakan tenant, permission, scope, decision, dan release authority ALOS yang sama. Tidak ada jalur authority paralel untuk product layer.

Semua akses ditolak secara default ketika principal, tenant, workspace, permission, atau scope tidak sesuai. Kepercayaan jaringan internal tidak menggantikan authentication dan authorization.
