"""Koneksi email bersama: IMAP (baca) dan SMTP (kirim).

Dipakai email_intake.py dan approval.py supaya logika koneksi tidak
duplikat. Kredensial selalu dari .env — tidak pernah ditulis di kode:

  IMAP_HOST, IMAP_PORT (993), IMAP_USERNAME, IMAP_PASSWORD,
  IMAP_FOLDER (INBOX)
  SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD,
  SMTP_USE_TLS (true)

Catatan Gmail: password di sini adalah App Password (bukan password
login biasa) dan IMAP harus aktif di Settings Gmail.
"""
import imaplib
import smtplib
from email.message import EmailMessage

from config_loader import env, env_bool


class MailError(Exception):
    pass


def _require(*keys):
    missing = [k for k in keys if not env(k)]
    if missing:
        raise MailError(f"variabel .env belum diisi: {', '.join(missing)}")


def imap_connect():
    _require("IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD")
    try:
        conn = imaplib.IMAP4_SSL(env("IMAP_HOST"), int(env("IMAP_PORT", "993")))
        conn.login(env("IMAP_USERNAME"), env("IMAP_PASSWORD"))
    except Exception as exc:
        raise MailError(f"login IMAP gagal: {exc}") from exc
    folder = env("IMAP_FOLDER", "INBOX")
    status, _ = conn.select(folder)
    if status != "OK":
        conn.logout()
        raise MailError(f"folder IMAP tidak bisa dibuka: {folder}")
    return conn


def send_email(to_addr, subject, body):
    """Kirim satu email teks. Raise MailError bila SMTP gagal.

    MAIL_DRY_RUN=true di .env: email tidak benar-benar dikirim, hanya
    dicetak — berguna saat pengujian tanpa mengganggu approver.
    """
    if env_bool("MAIL_DRY_RUN", False):
        print(f"[DRY-RUN] email ke {to_addr} | {subject}")
        return
    _require("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
    msg = EmailMessage()
    msg["From"] = env("SMTP_USERNAME")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(env("SMTP_PORT", "587"))
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(env("SMTP_HOST"), port, timeout=30)
        else:
            server = smtplib.SMTP(env("SMTP_HOST"), port, timeout=30)
            if env_bool("SMTP_USE_TLS", True):
                server.starttls()
        with server:
            server.login(env("SMTP_USERNAME"), env("SMTP_PASSWORD"))
            server.send_message(msg)
    except Exception as exc:
        raise MailError(f"kirim email ke {to_addr} gagal: {exc}") from exc
