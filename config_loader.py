"""Baca konfigurasi dari Config.xlsx (dinamis — jangan hardcode).

Config.xlsx yang dikirim Protelindo berisi:
  ThresholdAmount, FormsUrl, ExcelERPPath, ApproverEmail
Reviewer akan menjalankan ulang dengan Config.xlsx mereka sendiri,
jadi semua nilai di sini dibaca dari file, bukan ditulis di kode.
"""
import os
from pathlib import Path

import openpyxl


def load_env(env_path=".env"):
    """Muat file .env sederhana (KEY=value per baris) ke environment.

    Sengaja tanpa library tambahan (python-dotenv) supaya project gampang
    dijalankan di laptop mana pun. Nilai yang sudah ada di environment
    sistem TIDAK ditimpa — jadi orchestrator/CI bisa menyuntikkan
    kredensial sendiri lewat environment asli.
    """
    path = Path(env_path)
    if not path.exists():
        return
    # utf-8-sig: file .env yang dibuat Notepad/PowerShell di Windows
    # sering diawali BOM — tanpa ini, variabel baris pertama tidak terbaca.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(key, default=""):
    return os.environ.get(key, default).strip()


def env_bool(key, default=False):
    return env(key, str(default)).lower() in ("1", "true", "yes", "on")


def load_config(config_path="Config.xlsx"):
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config tidak ditemukan: {path.resolve()}")
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    config = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            config[str(row[0]).strip()] = row[1]
    threshold = int(str(config["ThresholdAmount"]).replace(".", "").replace(",", ""))
    return {
        "threshold_amount": threshold,
        "forms_url": str(config.get("FormsUrl", "")).strip(),
        "excel_erp_path": str(config.get("ExcelERPPath", "")).strip(),
        "approver_email": str(config.get("ApproverEmail", "")).strip(),
    }
