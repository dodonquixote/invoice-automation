"""Ekspor Excel dari database — mengikuti header template Protelindo.

  Invoices.xlsx      — 15 kolom (sheet Invoices_T)
  ERP_Dummy.xlsx     — 9 kolom: InvoiceNumber, VendorName, PO_Number, Amount,
                       Currency, PostingDate, ApprovalStatus, PostingRef, SubmittedAt
  ProcessLogs.xlsx   — 5 kolom (sheet Logs_T)
"""
from pathlib import Path

import openpyxl

from database import INVOICE_COLUMNS

ERP_COLUMNS = ["InvoiceNumber", "VendorName", "PO_Number", "Amount", "Currency",
               "PostingDate", "ApprovalStatus", "PostingRef", "SubmittedAt"]
LOG_COLUMNS = ["Timestamp", "CorrelationId", "Step", "Result", "Message"]


def _write(path, sheet_name, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def export_all(conn, out_dir="output"):
    """Tulis ketiga Excel dari database. Return folder output.

    Kegagalan menulis SATU file (mis. PermissionError karena file sedang
    dibuka di Excel — Windows mengunci file yang terbuka) tidak boleh
    menggagalkan seluruh run: file itu dilewati dengan peringatan jelas,
    file lain tetap ditulis. Database adalah sumber kebenaran; Excel
    hanya laporan, jadi ekspor bisa diulang kapan saja pada run berikut.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    invoices = conn.execute(
        f"SELECT {', '.join(INVOICE_COLUMNS)} FROM invoices ORDER BY CreatedAt"
    ).fetchall()
    posted = conn.execute(
        "SELECT InvoiceNumber, VendorName, PO_Number, Amount, Currency,"
        " PostedAt, ApprovalStatus, PostingRef, PostedAt FROM invoices"
        " WHERE PostedAt IS NOT NULL ORDER BY CreatedAt"
    ).fetchall()
    logs = conn.execute(
        "SELECT Timestamp, CorrelationId, Step, Result, Message"
        " FROM process_logs ORDER BY Timestamp"
    ).fetchall()
    jobs = [
        (out / "Invoices.xlsx", "Invoices_T", INVOICE_COLUMNS, invoices),
        (out / "ERP_Dummy.xlsx", "PostedInvoices", ERP_COLUMNS, posted),
        (out / "ProcessLogs.xlsx", "Logs_T", LOG_COLUMNS, logs),
    ]
    failed = []
    for path, sheet, headers, rows in jobs:
        try:
            _write(path, sheet, headers, rows)
        except OSError as exc:
            failed.append(path.name)
            print(f"ekspor: {path.name} tidak bisa ditulis ({exc}) — "
                  "file sedang dibuka? tutup dulu, ekspor diulang run berikut")
    if failed:
        print(f"ekspor: {len(failed)} file dilewati: {', '.join(failed)}")
    return out
