"""Pipeline utama otomasi invoice — satu file orkestrasi, tugas per modul.

Alur (sesuai brief Protelindo):
  email/folder -> OCR (vision_ocr) -> regex (extractor) -> database
  -> validasi PO (po_validation) -> approval email bila di atas ambang
  (approval) -> posting form ERP via browser (erp_posting)
  -> ekspor Excel dari database (exporters) -> arsip gambar ke
  output/Processed/<hasil>.

Mode jalan:
  python main.py                      email: ambil lampiran dari mailbox (.env)
  python main.py --source folder      proses semua gambar di folder inbox/
  python main.py --source folder --folder samples
  python main.py --wait-approval 10   setelah kirim permintaan approval,
                                      tunggu balasan sampai 10 menit sambil
                                      memeriksa mailbox berkala
  python main.py --watch 3            mode standby: ulangi siklus email
                                      tiap 3 menit (email yang masuk
                                      langsung diproses; Ctrl+C berhenti)
  python main.py --demo               alur inti dengan teks OCR contoh
                                      (tanpa kredensial apa pun)

Semua kredensial dari .env; semua nilai bisnis (threshold, URL form,
email approver) dari Config.xlsx. Tidak ada yang di-hardcode.
Kode keluar: 0 = run selesai (error per-invoice tercatat di ProcessLogs),
2 = konfigurasi/kredensial belum lengkap.
"""
import argparse
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import approval
from config_loader import env, load_config, load_env
from database import (find_by_invoice_number, get_invoice, init_db,
                      log_step, save_invoice, update_invoice)
from email_intake import IMAGE_EXTENSIONS, fetch_new_invoice_images
from erp_posting import PostingError, post_invoice
from exporters import export_all
from extractor import extract
from mail_client import MailError
from po_validation import load_purchase_orders, validate
from vision_ocr import VisionError, ocr_image

BASE = Path(__file__).resolve().parent
PROCESSED_DIR = BASE / "output" / "Processed"

# Teks OCR dari 3 invoice contoh — persis seperti tertulis di gambarnya.
DEMO_OCR_TEXTS = {
    "sample_invoice_1.jpg": (
        "INVOICE INV-2025-001\nVendor Name: PT Maju Jaya\n"
        "PO Number: PO-001\nAmount: 1200000 IDR"),
    "sample_invoice_2.jpg": (
        "INVOICE INV-2025-002\nVendor Name: PT Sukses Makmur\n"
        "PO Number: PO-002\nAmount: 2500000 IDR"),
    "sample_invoice_3.jpg": (
        "INVOICE INV-2025-003\nVendor Name: PT Tidak Ada\n"
        "PO Number: PO-999\nAmount: 950000 IDR"),
}


def new_correlation_id():
    return str(uuid.uuid4())[:8]


def to_project_relative(path):
    """Kembalikan path relatif terhadap folder proyek bila bisa.

    FilePath disimpan relatif (mis. 'output/Processed/POSTED/foto.jpg'),
    BUKAN absolut dari root drive ('C:\\...\\invoice-automation\\...'),
    supaya catatan di database & Invoices.xlsx tetap valid walau folder
    proyek dipindah atau dikopi ke lokasi/mesin lain — persis skenario
    reviewer menjalankan proyek ini di laptopnya sendiri. File di luar
    folder proyek (folder sumber eksternal) tetap disimpan absolut.
    """
    p = Path(path)
    try:
        return str(p.resolve().relative_to(BASE))
    except ValueError:
        return str(p)


def archive_file(conn, invoice, result_folder):
    """Pindahkan gambar invoice ke output/Processed/<hasil>.

    FilePath di database selalu diperbarui ke lokasi terakhir, jadi
    riwayat file bisa dilacak dari Invoices.xlsx.
    """
    src = Path(invoice["FilePath"])
    if not src.is_absolute():
        src = BASE / src  # FilePath relatif dihitung dari folder proyek
    dest_dir = PROCESSED_DIR / result_folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.exists() and src.resolve() != (dest_dir / src.name).resolve():
        dest = dest_dir / src.name
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
            counter += 1
        shutil.move(str(src), str(dest))
        update_invoice(conn, invoice["CorrelationId"],
                       FilePath=to_project_relative(dest))
        invoice["FilePath"] = to_project_relative(dest)
    log_step(conn, invoice["CorrelationId"], "archive", "OK",
             f"-> Processed/{result_folder}/{src.name}")


def post_and_finalize(conn, invoice, forms_url):
    """Posting satu invoice yang sudah boleh tayang ke form ERP."""
    corr = invoice["CorrelationId"]
    values = {
        "InvoiceNumber": invoice["InvoiceNumber"],
        "VendorName": invoice["VendorName"],
        "PO_Number": invoice["PO_Number"],
        "Amount": invoice["Amount"],
        "Currency": invoice["Currency"],
        "PostingDate": datetime.now().strftime("%Y-%m-%d"),
        "ApprovalStatus": invoice["ApprovalStatus"],
        "PostingRef": invoice["PostingRef"],
    }
    try:
        _, note = post_invoice(forms_url, values)
    except PostingError as exc:
        update_invoice(conn, corr, Status="POST_FAILED")
        log_step(conn, corr, "erp_posting", "FAIL", str(exc))
        archive_file(conn, invoice, "POST_FAILED")
        return False
    update_invoice(conn, corr,
                   PostedAt=datetime.now().isoformat(timespec="seconds"),
                   Status="POSTED")
    log_step(conn, corr, "erp_posting", "OK",
             f"PostingRef {invoice['PostingRef']}; {note}")
    archive_file(conn, invoice, "POSTED")
    return True


def process_image(conn, image_path, config, orders):
    """Proses satu gambar invoice dari OCR sampai keputusan akhir run ini."""
    corr = new_correlation_id()
    log_step(conn, corr, "intake", "OK", f"file: {to_project_relative(image_path)}")
    try:
        text = ocr_image(image_path)
    except VisionError as exc:
        log_step(conn, corr, "ocr", "FAIL", str(exc))
        save_invoice(conn, {
            "CorrelationId": corr, "FilePath": to_project_relative(image_path),
            "InvoiceNumber": None, "VendorName": None, "PO_Number": None,
            "Amount": None, "Currency": None, "MatchStatus": "OCR_FAILED",
            "NeedsApproval": 0, "ApprovalStatus": "NOT_REQUIRED",
            "PostingRef": None, "PostedAt": None, "Status": "FAILED",
        })
        archive_file(conn, get_invoice(conn, corr), "FAILED")
        return "FAILED"
    data = extract(text)
    log_step(conn, corr, "extract", "OK" if data["InvoiceNumber"] else "FAIL",
             f"hasil regex: {data}")
    if data["InvoiceNumber"] and find_by_invoice_number(conn, data["InvoiceNumber"]):
        log_step(conn, corr, "extract", "SKIP",
                 f"{data['InvoiceNumber']} sudah pernah diproses")
        return "SKIPPED"
    status, note = validate(data, orders)
    log_step(conn, corr, "validate_po", status, note)
    needs_approval = bool(
        data["Amount"] is not None and data["Amount"] > config["threshold_amount"])
    log_step(conn, corr, "approval_check",
             "NEEDS_APPROVAL" if needs_approval else "NO_APPROVAL",
             f"amount {data['Amount']} vs threshold {config['threshold_amount']}")
    posting_ref = f"POST-{data['InvoiceNumber']}" if data["InvoiceNumber"] else None
    if status != "MATCH":
        save_invoice(conn, {
            "CorrelationId": corr, "FilePath": to_project_relative(image_path),
            "InvoiceNumber": data["InvoiceNumber"],
            "VendorName": data["VendorName"], "PO_Number": data["PO_Number"],
            "Amount": data["Amount"], "Currency": data["Currency"],
            "MatchStatus": status, "NeedsApproval": int(needs_approval),
            "ApprovalStatus": "NOT_REQUIRED", "PostingRef": None,
            "PostedAt": None, "Status": "REJECTED",
        })
        archive_file(conn, get_invoice(conn, corr), "REJECTED")
        return "REJECTED"
    save_invoice(conn, {
        "CorrelationId": corr, "FilePath": to_project_relative(image_path),
        "InvoiceNumber": data["InvoiceNumber"],
        "VendorName": data["VendorName"], "PO_Number": data["PO_Number"],
        "Amount": data["Amount"], "Currency": data["Currency"],
        "MatchStatus": status, "NeedsApproval": int(needs_approval),
        "ApprovalStatus": "PENDING" if needs_approval else "NOT_REQUIRED",
        "PostingRef": posting_ref, "PostedAt": None,
        "Status": "AWAITING_APPROVAL" if needs_approval else "READY_TO_POST",
    })
    invoice = get_invoice(conn, corr)
    if needs_approval:
        approval.send_approval_request(conn, invoice, config["approver_email"])
        archive_file(conn, invoice, "AWAITING_APPROVAL")
        return "AWAITING_APPROVAL"
    return "POSTED" if post_and_finalize(conn, invoice, config["forms_url"]) \
        else "POST_FAILED"


def retry_failed_postings(conn, config):
    """Coba lagi posting yang sebelumnya gagal (Status POST_FAILED).

    Hanya invoice yang memang sudah boleh tayang: approval-nya APPROVED,
    atau sejak awal tidak membutuhkan approval. Kegagalan posting (form
    tidak bisa dijangkau, konfirmasi tidak terdeteksi, dsb.) bersifat
    sementara, jadi run/siklus berikutnya adalah momen mencobanya lagi —
    invoice tidak boleh hilang hanya karena satu percobaan gagal.
    """
    rows = conn.execute(
        "SELECT CorrelationId FROM invoices WHERE Status = 'POST_FAILED'"
    ).fetchall()
    for (corr_id,) in rows:
        inv = get_invoice(conn, corr_id)
        if inv and inv["ApprovalStatus"] in ("APPROVED", "NOT_REQUIRED"):
            print(f"mencoba lagi posting {inv['InvoiceNumber']}...")
            post_and_finalize(conn, inv, config["forms_url"])


def handle_approval_decisions(conn, config):
    """Tindaklanjuti balasan approver: posting yang disetujui, tutup yang ditolak."""
    try:
        changed = approval.check_approval_replies(conn)
    except MailError as exc:
        print(f"cek balasan approval gagal: {exc}")
        return
    for inv in changed:
        fresh = get_invoice(conn, inv["CorrelationId"])
        if fresh["ApprovalStatus"] == "APPROVED":
            post_and_finalize(conn, fresh, config["forms_url"])
        else:
            update_invoice(conn, fresh["CorrelationId"], Status="REJECTED")
            log_step(conn, fresh["CorrelationId"], "approval", "REJECTED",
                     "ditolak approver")
            archive_file(conn, fresh, "REJECTED")


def collect_files(source, folder):
    if source == "email":
        return fetch_new_invoice_images(BASE / "inbox")
    folder_path = Path(folder)
    if not folder_path.is_absolute():
        folder_path = BASE / folder_path
    if not folder_path.exists():
        raise FileNotFoundError(f"folder sumber tidak ada: {folder_path}")
    return sorted(p for p in folder_path.iterdir()
                  if p.suffix.lower() in IMAGE_EXTENSIONS)


def run_demo(config, orders):
    conn = init_db(str(BASE / "invoices.db"))
    print(f"Threshold dari Config.xlsx: Rp{config['threshold_amount']:,}")
    # Demo bekerja pada SALINAN gambar di folder demo_inbox, supaya file
    # asli di samples/ tidak ikut terpindah saat pengarsipan.
    demo_inbox = BASE / "demo_inbox"
    demo_inbox.mkdir(exist_ok=True)
    for filename, text in DEMO_OCR_TEXTS.items():
        work_copy = demo_inbox / filename
        original = BASE / "samples" / filename
        if original.exists():
            shutil.copy2(original, work_copy)
        corr = new_correlation_id()
        log_step(conn, corr, "intake", "OK", f"file: {filename} (demo)")
        data = extract(text)
        status, note = validate(data, orders)
        log_step(conn, corr, "validate_po", status, note)
        needs_approval = bool(
            data["Amount"] is not None
            and data["Amount"] > config["threshold_amount"])
        posting_ref = f"POST-{data['InvoiceNumber']}" if status == "MATCH" else None
        final_status = "READY_TO_POST" if status == "MATCH" else "REJECTED"
        save_invoice(conn, {
            "CorrelationId": corr,
            "FilePath": to_project_relative(work_copy),
            "InvoiceNumber": data["InvoiceNumber"],
            "VendorName": data["VendorName"], "PO_Number": data["PO_Number"],
            "Amount": data["Amount"], "Currency": data["Currency"],
            "MatchStatus": status, "NeedsApproval": int(needs_approval),
            "ApprovalStatus": "PENDING" if needs_approval else "NOT_REQUIRED",
            "PostingRef": posting_ref, "PostedAt": None, "Status": final_status,
        })
        invoice = get_invoice(conn, corr)
        archive_file(conn, invoice, final_status)
        print(f"- {data['InvoiceNumber']}: {status}, "
              f"perlu approval: {needs_approval}, status: {final_status}")
    out = export_all(conn, str(BASE / "output"))
    print(f"Ekspor selesai di {out}/ (Invoices, ERP_Dummy, ProcessLogs)")


def main():
    parser = argparse.ArgumentParser(description="Otomasi invoice Protelindo")
    parser.add_argument("--demo", action="store_true",
                        help="jalankan alur inti dengan teks OCR contoh")
    parser.add_argument("--source", choices=["email", "folder"],
                        default="email",
                        help="sumber gambar invoice (default: email)")
    parser.add_argument("--folder", default="inbox",
                        help="folder gambar bila --source folder")
    parser.add_argument("--config", default=str(BASE / "Config.xlsx"))
    parser.add_argument("--po", default=str(BASE / "PurchaseOrders.csv"))
    parser.add_argument("--wait-approval", type=int, default=None,
                        help="menit menunggu balasan approval (default dari"
                             " .env APPROVAL_WAIT_MINUTES atau 0)")
    parser.add_argument("--watch", type=float, default=None,
                        help="mode standby: ulangi siklus email tiap N menit")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print(f"Python 3.10+ dibutuhkan (terdeteksi "
              f"{sys.version_info.major}.{sys.version_info.minor}). "
              "Perbarui Python lalu jalankan lagi.")
        return 2
    load_env(BASE / ".env")
    try:
        config = load_config(args.config)
        orders = load_purchase_orders(args.po)
    except (FileNotFoundError, KeyError) as exc:
        print(f"Konfigurasi bermasalah: {exc}")
        return 2

    if args.demo:
        run_demo(config, orders)
        return 0

    conn = init_db(str(BASE / "invoices.db"))
    if args.watch:
        return run_watch(conn, config, orders, args)
    return run_cycle(conn, config, orders, args.source, args.folder,
                     args.wait_approval)


def run_cycle(conn, config, orders, source, folder, wait_arg):
    """Satu siklus penuh: balasan approval -> intake -> proses -> ekspor."""
    # 1) Balasan approval dari run sebelumnya diproses lebih dulu,
    #    lalu posting yang sempat gagal dicoba lagi.
    handle_approval_decisions(conn, config)
    retry_failed_postings(conn, config)
    # 2) Ambil invoice baru.
    try:
        files = collect_files(source, folder)
    except (MailError, FileNotFoundError) as exc:
        print(f"Intake gagal: {exc}")
        return 2
    print(f"{len(files)} gambar invoice untuk diproses")
    summary = {}
    for path in files:
        result = process_image(conn, path, config, orders)
        summary[result] = summary.get(result, 0) + 1
        print(f"- {Path(path).name}: {result}")
    # 3) Opsional: tunggu balasan approval dalam run yang sama.
    wait_minutes = wait_arg
    if wait_minutes is None:
        wait_minutes = int(env("APPROVAL_WAIT_MINUTES", "0") or 0)
    deadline = time.time() + wait_minutes * 60
    while wait_minutes > 0 and time.time() < deadline:
        from database import pending_approvals
        if not pending_approvals(conn):
            break
        print("menunggu balasan approval... (cek lagi 30 detik)")
        time.sleep(30)
        handle_approval_decisions(conn, config)
    # 4) Ekspor selalu di akhir run, apa pun hasilnya.
    out = export_all(conn, str(BASE / "output"))
    print(f"Ringkasan: {summary}")
    print(f"Ekspor selesai di {out}/ (Invoices, ERP_Dummy, ProcessLogs)")
    return 0


def run_watch(conn, config, orders, args):
    """Mode standby: ulangi siklus email terus-menerus (email = trigger).

    Setiap siklus memeriksa balasan approval lebih dulu, lalu mengambil
    invoice baru, jadi email yang masuk di antara dua siklus diproses
    pada siklus berikutnya dan balasan approver ditindaklanjuti tanpa
    perintah tambahan. Error satu siklus dicatat dan tidak mematikan
    program; berhenti bersih dengan Ctrl+C.
    """
    interval = max(1, int(args.watch * 60))
    print(f"Mode watch aktif: siklus tiap {args.watch:g} menit. "
          "Ctrl+C untuk berhenti.")
    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n=== siklus {cycle} "
                  f"({datetime.now().strftime('%H:%M:%S')}) ===")
            try:
                run_cycle(conn, config, orders, "email", args.folder, 0)
            except Exception as exc:  # siklus gagal tidak mematikan standby
                print(f"siklus {cycle} gagal: {exc} - lanjut siklus berikut")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMode watch dihentikan (Ctrl+C).")
    out = export_all(conn, str(BASE / "output"))
    print(f"Ekspor terakhir selesai di {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
