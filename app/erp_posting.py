"""Posting ERP lewat browser (Selenium): isi form simulasi ERP.

Form adalah Microsoft Form atau Google Form yang dibuat sendiri
(URL-nya di Config.xlsx -> FormsUrl). Karena reviewer bisa memakai
form mereka sendiri, pengisian TIDAK mengandalkan ID elemen yang
spesifik untuk satu form, melainkan:

  1. kumpulkan semua field teks yang terlihat di halaman,
  2. untuk tiap field, cari label pertanyaannya (teks pada elemen
     induk/ancestor terdekat — pola ini berlaku di Google Forms,
     Microsoft Forms, dan form HTML biasa),
  3. cocokkan label dengan kata kunci per data invoice
     (FIELD_KEYWORDS), isi nilainya,
  4. field yang labelnya tidak dikenal diisi menurut urutan standar
     sebagai cadangan terakhir,
  5. klik tombol Submit/Kirim, lalu tunggu teks konfirmasi.

Browser default TERLIHAT (visible) sesuai brief; set HEADLESS=true
di .env untuk menjalankannya tanpa jendela (mis. di orchestrator).
PostingRef yang dikirim diambil dari database — tidak dibuat ulang
di sini, supaya sama persis di form, ERP_Dummy.xlsx, dan Invoices.xlsx.
"""
import time

from config_loader import env_bool

# Urutan & kata kunci label untuk tiap data. Kata kunci yang lebih
# spesifik ditaruh lebih dulu (PostingRef sebelum Amount generik, dst).
FIELD_KEYWORDS = [
    ("PostingRef", ["posting ref", "posting reference", "referensi posting",
                    "reference", "ref"]),
    ("InvoiceNumber", ["invoice number", "invoice no", "nomor invoice",
                       "no invoice", "invoice"]),
    ("VendorName", ["vendor", "supplier", "nama vendor", "perusahaan"]),
    ("PO_Number", ["po number", "po no", "purchase order", "nomor po", "po"]),
    ("Amount", ["amount", "total amount", "nominal", "jumlah", "total"]),
    ("Currency", ["currency", "mata uang"]),
    ("PostingDate", ["posting date", "tanggal posting", "tanggal", "date"]),
    ("ApprovalStatus", ["approval status", "status approval", "approval",
                        "persetujuan"]),
]
SUBMIT_TEXTS = ["submit", "kirim", "send", "kirimkan"]
CONFIRM_TEXTS = ["recorded", "terima kasih", "thank", "response has been",
                 "tanggapan anda", "tanggapan kamu", "submitted",
                 "berhasil", "another response", "tanggapan lain"]


class PostingError(Exception):
    pass


def _build_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise PostingError(
            "selenium belum terinstall; jalankan: pip install -r requirements.txt"
        ) from exc
    options = Options()
    if env_bool("HEADLESS", False):
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    # Selenium Manager (bawaan Selenium >= 4.10) mengunduh driver yang
    # cocok secara otomatis — tidak perlu mengelola chromedriver manual.
    return webdriver.Chrome(options=options)


def _label_for(driver, element):
    """Ambil teks label pertanyaan untuk satu field (string, bisa '')."""
    try:
        return driver.execute_script(
            """
            let el = arguments[0];
            for (let depth = 0; depth < 8 && el; depth++) {
                el = el.parentElement;
                if (!el) break;
                let text = (el.innerText || '').trim();
                if (text) return text.split('\\n')[0].slice(0, 120);
            }
            return '';
            """, element) or ""
    except Exception:
        return ""


def _match_field(label):
    low = (label or "").lower()
    for field, keywords in FIELD_KEYWORDS:
        if any(kw in low for kw in keywords):
            return field
    return None


def post_invoice(forms_url, values):
    """Isi & kirim form ERP untuk satu invoice.

    values: dict berisi kunci FIELD_KEYWORDS (yang tidak ada di form
    akan dilewati). Return (True, pesan) bila terkirim, atau raise
    PostingError dengan penjelasan yang bisa dicatat ke ProcessLogs.
    """
    if not forms_url or "your-form-url" in forms_url:
        raise PostingError(
            "FormsUrl di Config.xlsx masih placeholder — buat form dulu"
            " lalu isi URL-nya")
    driver = _build_driver()
    try:
        driver.get(forms_url)
        time.sleep(3)  # beri waktu form selesai dirender
        from selenium.webdriver.common.by import By
        inputs = [
            el for el in driver.find_elements(
                By.CSS_SELECTOR, "input[type='text'], input[type='number'],"
                " textarea, input:not([type])")
            if el.is_displayed() and el.is_enabled()
        ]
        if not inputs:
            raise PostingError("tidak ada field teks yang ditemukan di form")
        filled = set()
        leftovers = []
        for el in inputs:
            field = _match_field(_label_for(driver, el))
            if field and field in values and field not in filled:
                el.clear()
                el.send_keys(str(values[field]))
                filled.add(field)
            else:
                leftovers.append(el)
        # Cadangan: isi field sisa menurut urutan standar FIELD_KEYWORDS.
        remaining = [f for f, _ in FIELD_KEYWORDS
                     if f in values and f not in filled]
        for el, field in zip(leftovers, remaining):
            el.clear()
            el.send_keys(str(values[field]))
            filled.add(field)
        missing = [f for f in values if f not in filled]
        # Cari & klik tombol submit.
        clicked = False
        candidates = driver.find_elements(
            By.CSS_SELECTOR, "button, div[role='button'], input[type='submit']")
        for el in candidates:
            text = (el.text or el.get_attribute("value") or "").strip().lower()
            if el.is_displayed() and any(t in text for t in SUBMIT_TEXTS):
                el.click()
                clicked = True
                break
        if not clicked:
            raise PostingError("tombol Submit/Kirim tidak ditemukan di form")
        # Tunggu halaman konfirmasi. Sinyal sukses terkuat untuk Google
        # Forms adalah URL berubah menjadi .../formResponse; teks
        # konfirmasi dicocokkan sebagai sinyal kedua.
        deadline = time.time() + 20
        while time.time() < deadline:
            time.sleep(1)
            if "formresponse" in driver.current_url.lower():
                break
            page_text = driver.page_source.lower()
            if any(t in page_text for t in CONFIRM_TEXTS):
                break
        else:
            # Simpan bukti visual untuk diagnosis sebelum menyerah:
            # screenshot + sumber halaman terakhir di output/.
            try:
                from pathlib import Path
                dbg = Path("output")
                dbg.mkdir(exist_ok=True)
                driver.save_screenshot(str(dbg / "posting_debug.png"))
                (dbg / "posting_debug.html").write_text(
                    driver.page_source, encoding="utf-8")
            except Exception:
                pass
            raise PostingError(
                "submit diklik tapi halaman konfirmasi tidak terdeteksi"
                f" (url terakhir: {driver.current_url})")
        note = f"terkirim; field terisi: {sorted(filled)}"
        if missing:
            note += f"; tidak ada field untuk: {missing}"
        return True, note
    finally:
        driver.quit()
