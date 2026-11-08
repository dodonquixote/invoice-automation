"""Approval via email: minta persetujuan & baca balasannya.

Aturan bisnis (dari brief):
  - hanya untuk invoice MATCH yang Amount-nya > ThresholdAmount,
  - email dikirim ke ApproverEmail dari Config.xlsx,
  - approver membalas dengan kata APPROVE atau REJECT — subjek email
    permintaan memuat CorrelationId, jadi balasan bisa dipasangkan ke
    invoice yang benar walau ada banyak permintaan bersamaan,
  - invoice yang belum APPROVED tidak boleh di-posting ke ERP.

Status di database:
  ApprovalStatus: PENDING -> APPROVED / REJECTED
  Status invoice: AWAITING_APPROVAL -> (diproses main.py setelah keputusan)
"""
import email
import re
from datetime import datetime, timedelta

from database import log_step, pending_approvals, update_invoice
from email_intake import _decode_subject
from mail_client import imap_connect, send_email

SUBJECT_PREFIX = "[INVOICE-APPROVAL]"
_APPROVE_RE = re.compile(r"\b(APPROVED|APPROVE|DISETUJUI|SETUJUI|SETUJU)\b", re.IGNORECASE)
_REJECT_RE = re.compile(r"\b(REJECT|REJECTED|TOLAK|DITOLAK)\b", re.IGNORECASE)


_QUOTE_MARKERS = (
    re.compile(r"^\s*On .+ wrote:\s*$"),          # Gmail bahasa Inggris
    re.compile(r"^\s*Pada .+ menulis:\s*$"),        # Gmail bahasa Indonesia
    re.compile(r"^\s*-+\s*Original Message", re.IGNORECASE),
)


def _strip_quoted(text):
    """Potong kutipan email asli dari sebuah balasan.

    Balasan email hampir selalu memuat kutipan email permintaan di
    bawahnya, dan email permintaan itu sendiri berisi kata APPROVE dan
    REJECT (petunjuk pengisian). Keputusan hanya boleh dibaca dari
    tulisan approver sendiri, jadi semua teks mulai dari baris penanda
    kutipan (baris '>', 'On ... wrote:', 'Pada ... menulis:') dibuang.
    """
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            break
        if any(p.match(line) for p in _QUOTE_MARKERS):
            break
        kept.append(line)
    return "\n".join(kept)


def parse_decision(text):
    """Tentukan keputusan dari teks balasan: 'APPROVED' / 'REJECTED' / None.

    Hanya tulisan approver yang dinilai (kutipan email asli dibuang
    dulu). Di dalam tulisan itu, REJECT dicek duluan supaya kalimat
    seperti "not approve, reject" tidak salah dibaca sebagai
    persetujuan.
    """
    if not text:
        return None
    own_text = _strip_quoted(text)
    if _REJECT_RE.search(own_text):
        return "REJECTED"
    if _APPROVE_RE.search(own_text):
        return "APPROVED"
    return None


def send_approval_request(conn, invoice, approver_email):
    """Kirim email permintaan approval untuk satu invoice.

    Return True bila terkirim (atau dry-run). Kegagalan SMTP dicatat ke
    ProcessLogs dan invoice DIBIARKAN PENDING supaya bisa dicoba lagi
    pada run berikutnya — tidak dianggap ditolak.
    """
    corr = invoice["CorrelationId"]
    subject = f"{SUBJECT_PREFIX} {corr} - {invoice['InvoiceNumber']}"
    body = (
        "Mohon persetujuan untuk invoice berikut.\n\n"
        f"Invoice Number : {invoice['InvoiceNumber']}\n"
        f"Vendor         : {invoice['VendorName']}\n"
        f"PO Number      : {invoice['PO_Number']}\n"
        f"Amount         : {invoice['Amount']} {invoice['Currency']}\n"
        f"Posting Ref    : {invoice['PostingRef']}\n"
        f"Correlation Id : {corr}\n\n"
        "Balas email ini dengan kata APPROVE untuk menyetujui atau\n"
        "REJECT untuk menolak. Pastikan subjek email tidak diubah.\n"
    )
    try:
        send_email(approver_email, subject, body)
    except Exception as exc:
        log_step(conn, corr, "approval_request", "FAIL", str(exc))
        return False
    log_step(conn, corr, "approval_request", "SENT",
             f"ke {approver_email}, amount {invoice['Amount']}")
    return True


def _message_text(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                body += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True) or b""
        body = payload.decode("utf-8", errors="replace")
    return body


def check_approval_replies(conn):
    """Pindai balasan approver; perbarui DB. Return list invoice berubah.

    Hanya invoice berstatus PENDING yang dicocokkan, berdasarkan
    CorrelationId di subjek balasan. Email balasan ditandai Seen
    setelah keputusan tercatat.
    """
    pending = {inv["CorrelationId"]: inv for inv in pending_approvals(conn)}
    if not pending:
        return []
    changed = []
    imap = imap_connect()
    try:
        # Pindai email 2 hari terakhir, BUKAN hanya yang UNSEEN: balasan
        # approval tidak boleh hilang hanya karena sempat terbuka di Gmail
        # atau dipindahkan dari folder Spam (yang mengubah status bacanya).
        # Pemrosesan ganda dicegah karena subjek wajib memuat CorrelationId
        # yang masih berstatus PENDING; sesudah keputusan tercatat, invoice
        # itu keluar dari daftar pending.
        since = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
        status, data = imap.search(None, f'(SINCE "{since}")')
        if status != "OK":
            return []
        for msg_id in data[0].split():
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode_subject(msg.get("Subject"))
            if SUBJECT_PREFIX not in subject:
                continue
            corr = next((c for c in pending if c in subject), None)
            if corr is None:
                continue
            decision = parse_decision(subject + "\n" + _message_text(msg))
            if decision is None:
                log_step(conn, corr, "approval_reply", "UNKNOWN",
                         f"balasan tanpa kata APPROVE/REJECT: {subject}")
                continue
            update_invoice(conn, corr, ApprovalStatus=decision)
            log_step(conn, corr, "approval_reply", decision,
                     f"dari {msg.get('From')}")
            imap.store(msg_id, "+FLAGS", "\\Seen")
            changed.append({**pending[corr], "ApprovalStatus": decision})
            pending.pop(corr)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return changed
