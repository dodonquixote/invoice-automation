"""Ekstraksi data invoice dari teks hasil OCR — pakai regex, BUKAN LLM.

Format dasar pada 3 invoice contoh (terverifikasi dari gambarnya):
    INVOICE INV-2025-001
    Vendor Name: PT Maju Jaya
    PO Number: PO-001
    Amount: 1200000 IDR

Invoice asli di lapangan tidak selalu memakai label & format persis
seperti contoh, jadi pola dibuat toleran:
  - label alternatif diterima (Invoice No., Supplier, Nomor PO,
    Total, Jumlah, label bahasa Indonesia),
  - huruf besar/kecil dan spasi di sekitar ':' bebas,
  - nominal menerima pemisah ribuan '.' atau ',' dan desimal
    ',00' / '.00' ala Indonesia maupun internasional.

Field yang tidak ketemu bernilai None — jangan ditebak, biar ketahuan
di ProcessLogs dan bisa diperbaiki regex-nya.
"""
import re

# Label nominal dicoba berurutan: yang paling spesifik didahulukan.
# "Total" diberi negative lookbehind agar tidak tertangkap dari
# kata "Subtotal".
_AMOUNT_LABELS = [
    r"Amount",
    r"Grand\s*Total",
    r"Total\s*Bayar",
    r"Total\s*Amount",
    r"(?<![A-Za-z])Total",
    r"Jumlah",
    r"Nominal",
]


def _clean_amount(raw):
    """Ubah teks nominal menjadi int rupiah utuh.

    '1200000' -> 1200000        '1.200.000'    -> 1200000
    '1,200,000' -> 1200000      '1.200.000,00' -> 1200000
    '1,200,000.00' -> 1200000

    Aturan: bila '.' dan ',' sama-sama ada, pemisah yang muncul
    TERAKHIR adalah pemisah desimal, yang lain pemisah ribuan. Bila
    hanya ada satu jenis pemisah: kelompok terakhir berisi tepat
    3 digit (atau pemisah muncul >1 kali) berarti pemisah ribuan;
    1-2 digit berarti desimal. Desimal ',00' dibuang; desimal bukan
    nol dibulatkan ke rupiah terdekat — nominal PO selalu bulat,
    jadi selisih sen terbaca sebagai AMOUNT_MISMATCH, bukan lolos.
    """
    s = re.sub(r"\s", "", raw.strip())
    if not s:
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        dec, thou = (".", ",") if last_dot > last_comma else (",", ".")
        int_part, _, frac = s.rpartition(dec)
        value = float(int_part.replace(thou, "") + "." + (frac or "0"))
    elif last_dot >= 0 or last_comma >= 0:
        sep = "." if last_dot >= 0 else ","
        parts = s.split(sep)
        if len(parts) > 2 or len(parts[-1]) == 3:
            value = float("".join(parts))
        else:
            value = float(parts[0] + "." + (parts[1] if len(parts) > 1 else "0"))
    else:
        value = float(s)
    return int(round(value))


def _extract_amount(text):
    """Cari nominal dari label-label yang dikenal. Return (amount, currency)."""
    for label in _AMOUNT_LABELS:
        m = re.search(
            label + r"\s*:\s*(?:Rp\.?\s*)?(?:([A-Za-z]{3})\s*)?"
                    r"([\d][\d.,]*)(?:\s*([A-Za-z]{3}))?",
            text, re.IGNORECASE,
        )
        if m:
            code = m.group(1) or m.group(3)
            return _clean_amount(m.group(2)), (code or "IDR").upper()
    return None, None


def extract(text):
    """Kembalikan dict InvoiceNumber, VendorName, PO_Number, Amount, Currency."""
    invoice = re.search(
        r"\bINVOICE\s*(?:NO\.?|NUMBER|#)?\s*:?\s*([A-Za-z0-9][A-Za-z0-9\-/]*)",
        text, re.IGNORECASE)
    vendor = re.search(
        r"(?:Vendor\s*Name|Nama\s*Vendor|Nama\s*Supplier|Vendor|Supplier)"
        r"\s*:\s*(.+)", text, re.IGNORECASE)
    po = re.search(
        r"(?:PO\s*(?:Number|No\.?)|Nomor\s*PO|No\.?\s*PO|PO)"
        r"\s*:\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
    amount, currency = _extract_amount(text)
    return {
        "InvoiceNumber": invoice.group(1).strip() if invoice else None,
        "VendorName": vendor.group(1).strip() if vendor else None,
        "PO_Number": po.group(1).strip().upper() if po else None,
        "Amount": amount,
        "Currency": currency,
    }
