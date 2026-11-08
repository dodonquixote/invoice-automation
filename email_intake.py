"""Email intake (IMAP): baca email baru berlampiran gambar invoice.

Alur:
  - login IMAP (kredensial dari .env lewat mail_client),
  - cari email UNSEEN di folder IMAP_FOLDER,
  - untuk tiap email: simpan lampiran gambar (.jpg/.jpeg/.png) ke folder
    inbox lokal; email ditandai sudah dibaca (flag Seen) HANYA setelah
    lampirannya tersimpan, supaya email tidak hilang kalau proses gagal
    di tengah jalan,
  - kembalikan daftar path file yang berhasil disimpan.

Brief mengizinkan dua mode: otomasi mengambil sendiri email baru
(mode ini, dijalankan berkala oleh orchestrator/task scheduler), atau
email dikirim dulu lalu script dijalankan manual. Modul ini mendukung
keduanya karena sifatnya one-shot per pemanggilan.
"""
import email
import re
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

from mail_client import MailError, imap_connect

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _safe_filename(name, fallback):
    name = Path(name or fallback).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or fallback


def _decode_subject(raw_subject):
    parts = decode_header(raw_subject or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _already_archived(filename):
    """True bila file bernama sama sudah diarsipkan ke output/Processed/<status>."""
    root = Path("output") / "Processed"
    return root.exists() and any(root.glob(f"*/{filename}"))


def _download_attachments(conn, ids, inbox, mark_seen, skip_archived):
    """Simpan lampiran gambar dari daftar id email. Return list[Path]."""
    saved = []
    for msg_id in ids:
        status, msg_data = conn.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = _decode_subject(msg.get("Subject"))
        attachments_saved = 0
        for part in msg.walk():
            filename = part.get_filename()
            if not filename:
                continue
            if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            # Terima lampiran (attachment) DAN gambar sematan (inline):
            # aplikasi email di HP lazim mengirim foto invoice sebagai
            # inline image (multipart/related), bukan attachment — keduanya
            # adalah cara sah mengirim invoice. Syarat nama file + ekstensi
            # gambar menyaring bagian email yang bukan berkas gambar.
            disposition = str(part.get("Content-Disposition") or "").lower()
            if disposition and "attachment" not in disposition \
                    and "inline" not in disposition:
                continue
            safe = _safe_filename(
                filename, f"invoice_{msg_id.decode()}_{attachments_saved}.jpg")
            if skip_archived and ((inbox / safe).exists() or _already_archived(safe)):
                continue  # sudah pernah diambil/diproses; jangan duplikat
            dest = inbox / safe
            counter = 1
            while dest.exists():  # jangan timpa file lama
                dest = inbox / f"{dest.stem}_{counter}{dest.suffix}"
                counter += 1
            dest.write_bytes(part.get_payload(decode=True) or b"")
            saved.append(dest)
            attachments_saved += 1
            print(f"intake: '{subject}' -> {dest.name}")
        if attachments_saved and mark_seen:
            conn.store(msg_id, "+FLAGS", "\\Seen")
    return saved


def fetch_new_invoice_images(inbox_dir):
    """Ambil lampiran gambar invoice dari email. Return list[Path].

    Jalur utama: email UNSEEN. Bila tidak ada yang UNSEEN, jalur cadangan
    memindai email 2 hari terakhir apa pun status bacanya - invoice tidak
    boleh gagal diproses hanya karena emailnya sempat terbuka di Gmail
    (yang mengubahnya menjadi terbaca). Duplikasi pada jalur cadangan
    dicegah dengan melewati nama file yang sudah terarsip di Processed,
    dan pemrosesan sendiri idempoten berdasarkan InvoiceNumber.

    Raise MailError bila koneksi/login IMAP gagal total.
    """
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    conn = imap_connect()
    try:
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            raise MailError("pencarian email UNSEEN gagal")
        saved = _download_attachments(conn, data[0].split(), inbox,
                                      mark_seen=True, skip_archived=False)
        if not saved:
            since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
            status, data = conn.search(None, f'(SINCE "{since}")')
            if status == "OK":
                saved = _download_attachments(conn, data[0].split(), inbox,
                                              mark_seen=False, skip_archived=True)
        return saved
    finally:
        try:
            conn.logout()
        except Exception:
            pass
