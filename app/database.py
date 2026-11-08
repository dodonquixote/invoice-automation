"""Database SQLite — sumber kebenaran; file Excel hanya ekspor dari sini.

Skema mengikuti persis header template yang dikirim Protelindo
(terverifikasi dari file template di folder tes):
  Invoices.xlsx  — 15 kolom, sheet Invoices_T
  ProcessLogs.xlsx — 5 kolom (Timestamp, CorrelationId, Step, Result, Message)
"""
import sqlite3
from datetime import datetime
from pathlib import Path

INVOICE_COLUMNS = [
    "CorrelationId", "FilePath", "InvoiceNumber", "VendorName", "PO_Number",
    "Amount", "Currency", "MatchStatus", "NeedsApproval", "ApprovalStatus",
    "PostingRef", "PostedAt", "Status", "CreatedAt", "UpdatedAt",
]


def init_db(db_path="invoices.db"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            CorrelationId TEXT PRIMARY KEY, FilePath TEXT, InvoiceNumber TEXT,
            VendorName TEXT, PO_Number TEXT, Amount INTEGER, Currency TEXT,
            MatchStatus TEXT, NeedsApproval INTEGER, ApprovalStatus TEXT,
            PostingRef TEXT, PostedAt TEXT, Status TEXT,
            CreatedAt TEXT, UpdatedAt TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS process_logs (
            Timestamp TEXT, CorrelationId TEXT, Step TEXT,
            Result TEXT, Message TEXT)"""
    )
    conn.commit()
    return conn


def log_step(conn, correlation_id, step, result, message=""):
    conn.execute(
        "INSERT INTO process_logs VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"),
         correlation_id, step, result, message),
    )
    conn.commit()


def get_invoice(conn, correlation_id):
    cur = conn.execute(
        "SELECT * FROM invoices WHERE CorrelationId = ?", (correlation_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def update_invoice(conn, correlation_id, **fields):
    fields["UpdatedAt"] = datetime.now().isoformat(timespec="seconds")
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE invoices SET {assignments} WHERE CorrelationId = ?",
        (*fields.values(), correlation_id),
    )
    conn.commit()


def find_by_invoice_number(conn, invoice_number):
    cur = conn.execute(
        "SELECT * FROM invoices WHERE InvoiceNumber = ?", (invoice_number,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def pending_approvals(conn):
    cur = conn.execute(
        "SELECT * FROM invoices WHERE ApprovalStatus = 'PENDING'"
        " AND Status = 'AWAITING_APPROVAL'")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def save_invoice(conn, row):
    now = datetime.now().isoformat(timespec="seconds")
    row = dict(row)
    row.setdefault("CreatedAt", now)
    row["UpdatedAt"] = now
    cols = ", ".join(INVOICE_COLUMNS)
    placeholders = ", ".join("?" * len(INVOICE_COLUMNS))
    conn.execute(
        f"INSERT OR REPLACE INTO invoices ({cols}) VALUES ({placeholders})",
        [row.get(c) for c in INVOICE_COLUMNS],
    )
    conn.commit()
