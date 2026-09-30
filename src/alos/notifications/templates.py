"""Indonesian email templates for ALOS corporate notifications."""

from __future__ import annotations

ROLE_HUMAN_NAMES: dict[str, str] = {
    "IT_ADMIN": "Administrator TI",
    "EXECUTIVE": "Eksekutif",
    "DIVISION_LEAD": "Pimpinan Divisi",
    "DIVISION_MEMBER": "Anggota Divisi",
}


def human_role_name(role: str) -> str:
    return ROLE_HUMAN_NAMES.get(role, role)


def render_activation_email(
    *,
    employee_name: str,
    workspace_name: str,
    role: str,
    activation_url: str,
) -> tuple[str, str, str]:
    """Render subject, plain text, and HTML for account activation invitation."""
    subject = "Aktifkan Akun ALOS Anda"
    role_label = human_role_name(role)
    name = employee_name.strip() or "Karyawan"

    text = f"""Halo {name},

Akun ALOS Anda telah dibuat.

Ruang Kerja: {workspace_name}
Peran: {role_label}

Klik tautan berikut untuk mengaktifkan akun dan menentukan kata sandi Anda:
{activation_url}

Tautan ini berlaku selama 24 jam dan hanya dapat digunakan satu kali.
Kata sandi dibuat sendiri oleh Anda pada halaman aktivasi demi menjaga keamanan akun.

Jika Anda tidak mengenali permintaan ini, silakan abaikan email ini.

Salam,
ALOS
"""

    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <title>{subject}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      line-height: 1.6;
      color: #1e293b;
      background-color: #f8fafc;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      max-width: 560px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 8px;
      border: 1px solid #e2e8f0;
      padding: 32px;
    }}
    .header {{
      font-size: 20px;
      font-weight: 700;
      color: #0f172a;
      margin-bottom: 16px;
      border-bottom: 2px solid #3b82f6;
      padding-bottom: 12px;
    }}
    .info-box {{
      background-color: #f1f5f9;
      border-left: 4px solid #3b82f6;
      padding: 12px 16px;
      margin: 20px 0;
      border-radius: 4px;
    }}
    .btn {{
      display: inline-block;
      background-color: #2563eb;
      color: #ffffff !important;
      text-decoration: none;
      padding: 12px 24px;
      border-radius: 6px;
      font-weight: 600;
      margin: 24px 0;
    }}
    .footer {{
      font-size: 13px;
      color: #64748b;
      margin-top: 32px;
      border-top: 1px solid #e2e8f0;
      padding-top: 16px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">ALOS</div>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Akun ALOS Anda telah dibuat.</p>
    <div class="info-box">
      <p style="margin: 0 0 6px 0;"><strong>Ruang Kerja:</strong> {workspace_name}</p>
      <p style="margin: 0;"><strong>Peran:</strong> {role_label}</p>
    </div>
    <p>Klik tombol berikut untuk mengaktifkan akun dan menentukan kata sandi Anda:</p>
    <p style="text-align: center;">
      <a href="{activation_url}" class="btn" target="_blank" rel="noopener noreferrer">
        Aktifkan Akun
      </a>
    </p>
    <p style="font-size: 13px; color: #64748b; word-break: break-all;">
      Atau salin tautan berikut ke peramban Anda:<br>{activation_url}
    </p>
    <p>
      <em>Tautan ini berlaku selama 24 jam dan hanya dapat digunakan satu kali.
      Kata sandi dibuat sendiri oleh Anda pada halaman aktivasi demi menjaga keamanan akun.</em>
    </p>
    <div class="footer">
      <p>Jika Anda tidak mengenali permintaan ini, silakan abaikan pesan ini.</p>
      <p style="margin: 0;">&copy; ALOS &bull; Sistem Operasi Perusahaan</p>
    </div>
  </div>
</body>
</html>
"""
    return subject, text, html


def render_password_reset_email(
    *,
    display_name: str,
    reset_url: str,
    ttl_minutes: int = 60,
) -> tuple[str, str, str]:
    """Render subject, plain text, and HTML for self-service password reset."""
    subject = "Atur Ulang Kata Sandi ALOS Anda"
    name = display_name.strip() or "Pengguna"

    text = f"""Halo {name},

Permintaan pengaturan ulang kata sandi akun ALOS Anda telah diterima.

Klik tautan berikut untuk menentukan kata sandi baru Anda:
{reset_url}

Tautan ini berlaku selama {ttl_minutes} menit dan hanya dapat digunakan satu kali.

Jika Anda tidak meminta pengaturan ulang kata sandi, abaikan email ini. Akun Anda tetap aman.

Salam,
ALOS
"""

    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <title>{subject}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      line-height: 1.6;
      color: #1e293b;
      background-color: #f8fafc;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      max-width: 560px;
      margin: 0 auto;
      background: #ffffff;
      border-radius: 8px;
      border: 1px solid #e2e8f0;
      padding: 32px;
    }}
    .header {{
      font-size: 20px;
      font-weight: 700;
      color: #0f172a;
      margin-bottom: 16px;
      border-bottom: 2px solid #3b82f6;
      padding-bottom: 12px;
    }}
    .btn {{
      display: inline-block;
      background-color: #2563eb;
      color: #ffffff !important;
      text-decoration: none;
      padding: 12px 24px;
      border-radius: 6px;
      font-weight: 600;
      margin: 24px 0;
    }}
    .footer {{
      font-size: 13px;
      color: #64748b;
      margin-top: 32px;
      border-top: 1px solid #e2e8f0;
      padding-top: 16px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">ALOS</div>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Permintaan pengaturan ulang kata sandi akun ALOS Anda telah diterima.</p>
    <p>Klik tombol berikut untuk menentukan kata sandi baru Anda:</p>
    <p style="text-align: center;">
      <a href="{reset_url}" class="btn" target="_blank" rel="noopener noreferrer">
        Atur Ulang Kata Sandi
      </a>
    </p>
    <p style="font-size: 13px; color: #64748b; word-break: break-all;">
      Atau salin tautan berikut ke peramban Anda:<br>{reset_url}
    </p>
    <p>
      <em>Tautan ini berlaku selama {ttl_minutes} menit dan hanya dapat digunakan satu kali.</em>
    </p>
    <div class="footer">
      <p>Jika Anda tidak meminta pengaturan ulang kata sandi, silakan abaikan email ini.</p>
      <p style="margin: 0;">&copy; ALOS &bull; Sistem Operasi Perusahaan</p>
    </div>
  </div>
</body>
</html>
"""
    return subject, text, html


def render_account_suspended_email(
    *,
    display_name: str,
    reason: str | None = None,
) -> tuple[str, str, str]:
    subject = "Pemberitahuan Penonaktifan Akun ALOS"
    name = display_name.strip() or "Pengguna"
    reason_text = f" Alasan: {reason}" if reason else ""
    text = f"""Halo {name},

Akun ALOS Anda telah dinonaktifkan (suspended) oleh Administrator TI.{reason_text}
Sesi aktif Anda telah dicabut. Hubungi tim TI perusahaan Anda jika ada pertanyaan.

Salam,
ALOS
"""
    html = f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><title>{subject}</title></head>
<body style="font-family: sans-serif; line-height: 1.6; color: #1e293b; padding: 24px;">
  <div style="max-width: 560px; margin: 0 auto; background: #fff; padding: 24px;
              border: 1px solid #e2e8f0; border-radius: 8px;">
    <h2 style="color: #b91c1c; margin-top: 0;">Pemberitahuan Penonaktifan Akun</h2>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Akun ALOS Anda telah dinonaktifkan sementara oleh Administrator TI.{reason_text}</p>
    <p>Seluruh sesi aktif telah dicabut. Silakan hubungi tim TI perusahaan Anda.</p>
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
    <p style="font-size: 12px; color: #64748b;">&copy; ALOS</p>
  </div>
</body>
</html>"""
    return subject, text, html


def render_account_reactivated_email(
    *,
    display_name: str,
) -> tuple[str, str, str]:
    subject = "Pemberitahuan Pengaktifan Kembali Akun ALOS"
    name = display_name.strip() or "Pengguna"
    text = f"""Halo {name},

Akun ALOS Anda telah diaktifkan kembali. Anda kini dapat masuk kembali.

Salam,
ALOS
"""
    html = f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><title>{subject}</title></head>
<body style="font-family: sans-serif; line-height: 1.6; color: #1e293b; padding: 24px;">
  <div style="max-width: 560px; margin: 0 auto; background: #fff; padding: 24px;
              border: 1px solid #e2e8f0; border-radius: 8px;">
    <h2 style="color: #15803d; margin-top: 0;">Akun ALOS Diaktifkan Kembali</h2>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Akun ALOS Anda telah diaktifkan kembali. Anda kini dapat masuk kembali ke sistem.</p>
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
    <p style="font-size: 12px; color: #64748b;">&copy; ALOS</p>
  </div>
</body>
</html>"""
    return subject, text, html


def render_password_changed_email(
    *,
    display_name: str,
) -> tuple[str, str, str]:
    subject = "Kata Sandi ALOS Anda Telah Diperbarui"
    name = display_name.strip() or "Pengguna"
    text = f"""Halo {name},

Kata sandi akun ALOS Anda baru saja berhasil diperbarui. Seluruh sesi sebelumnya telah dicabut.
Jika Anda tidak melakukan perubahan ini, segera hubungi Administrator TI perusahaan Anda.

Salam,
ALOS
"""
    html = f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="UTF-8"><title>{subject}</title></head>
<body style="font-family: sans-serif; line-height: 1.6; color: #1e293b; padding: 24px;">
  <div style="max-width: 560px; margin: 0 auto; background: #fff; padding: 24px;
              border: 1px solid #e2e8f0; border-radius: 8px;">
    <h2 style="color: #0f172a; margin-top: 0;">Kata Sandi Berhasil Diperbarui</h2>
    <p>Halo <strong>{name}</strong>,</p>
    <p>Kata sandi akun ALOS Anda telah berhasil diperbarui. Seluruh sesi telah dicabut.</p>
    <p style="color: #b91c1c;">
      Jika Anda tidak melakukan tindakan ini, segera hubungi Administrator TI Anda.
    </p>
    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
    <p style="font-size: 12px; color: #64748b;">&copy; ALOS</p>
  </div>
</body>
</html>"""
    return subject, text, html
