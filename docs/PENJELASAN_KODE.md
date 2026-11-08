# Penjelasan Kode — Bekal Interview Protelindo

Dokumen ini buat kamu: penjelasan tiap modul dengan bahasa sederhana,
keputusan desain + alasannya, dan prediksi pertanyaan reviewer beserta
cara menjawabnya. Baca sambil membuka file-nya satu per satu.

## Gambaran besar (hafalkan alur ini)

> Email invoice masuk → script ambil lampiran gambar (IMAP) → gambar
> dibaca Google Vision jadi teks → regex mengambil 5 field → disimpan
> ke database SQLite → divalidasi ke PurchaseOrders.csv → kalau nominal
> di atas Rp1.000.000 (dari Config.xlsx) kirim email approval → kalau
> disetujui (atau tidak perlu approval) Selenium mengisi form ERP →
> Excel diekspor dari database → gambar dipindah ke folder Processed
> sesuai hasilnya. Semua langkah dicatat di ProcessLogs.

## Penjelasan per modul

### `main.py` — orkestrator
Hanya mengatur urutan, tidak berisi logika detail. Fungsinya:
- `process_image()` — satu gambar dari OCR sampai keputusan akhir.
- `post_and_finalize()` — posting ke form lalu update status POSTED.
- `archive_file()` — memindahkan gambar ke `output/Processed/<hasil>`
  dan memperbarui FilePath di database.
- `handle_approval_decisions()` — memproses balasan approver: yang
  APPROVED diposting, yang REJECTED ditutup.
- Mode `--demo` memakai teks OCR contoh yang sudah diverifikasi dari
  gambar, supaya alur inti bisa ditunjukkan tanpa kredensial.
- Idempotensi: kalau InvoiceNumber sudah ada di database, file dilewati
  (tidak diproses dua kali).

### `config_loader.py`
- `load_config()` membaca Config.xlsx baris per baris (Key → Value):
  ThresholdAmount, FormsUrl, ExcelERPPath, ApproverEmail. Threshold
  dibersihkan dari titik/koma lalu jadi int.
- `load_env()` membaca `.env` manual (tanpa library python-dotenv,
  supaya dependensi minimal) dan TIDAK menimpa environment yang sudah
  ada — jadi di server, kredensial bisa disuntikkan dari luar.

### `vision_ocr.py`
- Jalur utama: REST API `images:annotate?key=API_KEY` dengan fitur
  `DOCUMENT_TEXT_DETECTION` (paling cocok untuk dokumen/invoice).
  Gambar dikirim sebagai base64 di body JSON.
- Jalur cadangan: SDK `google-cloud-vision` bila yang tersedia adalah
  file service account (GOOGLE_APPLICATION_CREDENTIALS).
- Semua kegagalan dibungkus `VisionError` → pemanggil mencatatnya ke
  ProcessLogs dan lanjut ke invoice berikutnya.

### `extractor.py` (regex — sesuai brief, bukan LLM)
- InvoiceNumber: `INVOICE\s+([A-Za-z0-9\-]+)`
- VendorName: setelah `Vendor Name:`
- PO_Number: setelah `PO Number:`, di-uppercase.
- Amount: angka setelah `Amount:`, boleh diawali `Rp`, pemisah ribuan
  titik/koma dibuang (`1.200.000` → 1200000).
- Currency: 3 huruf setelah nominal, default IDR.
- Field yang tidak ketemu = None, tidak ditebak → ketahuan di log.

### `po_validation.py`
Mengembalikan satu dari: `MATCH`, `PO_NOT_FOUND`, `VENDOR_MISMATCH`,
`AMOUNT_MISMATCH`, `INCOMPLETE`. Urutan cek: kelengkapan field → PO
ada? → vendor sama (case-insensitive)? → nominal sama persis?

### `database.py`
- SQLite, 2 tabel: `invoices` (15 kolom persis template Invoices.xlsx)
  dan `process_logs` (5 kolom persis template ProcessLogs.xlsx).
- `save_invoice`, `get_invoice`, `update_invoice`, `log_step`,
  `find_by_invoice_number` (untuk idempotensi), `pending_approvals`.
- Prinsipnya: database = sumber kebenaran; Excel hanya ekspor.

### `email_intake.py` + `mail_client.py`
- IMAP SSL, cari `UNSEEN`, jalan ke tiap bagian MIME, simpan lampiran
  .jpg/.jpeg/.png saja, nama file dibersihkan dari karakter aneh, tidak
  menimpa file lama (ditambah suffix angka).
- Email ditandai `\Seen` HANYA setelah lampiran tersimpan — kalau
  proses gagal di tengah, email tetap unread dan akan diambil lagi.
- `mail_client.send_email` — SMTP (STARTTLS di 587, SSL di 465), ada
  mode `MAIL_DRY_RUN` untuk pengujian tanpa benar-benar mengirim.

### `approval.py`
- Subjek email: `[INVOICE-APPROVAL] <CorrelationId> - <InvoiceNumber>`.
  CorrelationId adalah kunci pasangan: balasan dicocokkan lewat ID di
  subjek, jadi banyak permintaan bersamaan tidak tertukar.
- `parse_decision()` — regex kata APPROVE/SETUJU vs REJECT/TOLAK;
  REJECT dicek duluan supaya "not approve, reject" tidak salah baca.
- `check_approval_replies()` hanya memproses invoice yang masih
  PENDING; keputusan ditulis ke database + log.

### `erp_posting.py` (Selenium)
- Tidak mengandalkan ID elemen khas satu form: script mengumpulkan
  semua field teks yang terlihat, mencari label pertanyaannya dari
  teks elemen induk (berlaku di Google Forms, Microsoft Forms, dan
  HTML biasa), lalu mencocokkan label dengan kata kunci per field
  (`FIELD_KEYWORDS`, yang spesifik didahulukan — PostingRef sebelum
  Amount). Cadangan terakhir: isi menurut urutan standar.
- Tombol submit dicari dari teksnya (Submit/Kirim/Send), lalu menunggu
  teks konfirmasi di halaman.
- Browser terlihat secara default (syarat brief); `HEADLESS=true`
  untuk jalan tanpa jendela. Driver diatur Selenium Manager bawaan —
  tidak perlu download chromedriver manual.
- PostingRef diambil dari database, tidak dibuat ulang di sini —
  itulah yang menjamin nilainya sama di form, ERP_Dummy, dan Invoices.

### `exporters.py`
Menulis ulang 3 file Excel dari database di akhir setiap run:
Invoices (15 kolom), ERP_Dummy (hanya invoice yang PostedAt terisi —
9 kolom), ProcessLogs (semua catatan langkah).

## Keputusan desain yang mungkin ditanya

1. **Kenapa SQLite?** Satu file, tanpa server, cukup untuk volume
   invoice seperti ini, dan reviewer bisa menjalankannya di laptop
   mana pun tanpa setup database. Naik kelasnya gampang (SQL-nya
   standar) bila nanti perlu PostgreSQL.
2. **Kenapa API key lewat REST, bukan SDK saja?** Brief mengirim API
   key; REST adalah cara langsung memakainya. SDK tetap didukung
   sebagai cadangan untuk service account.
3. **Kenapa tidak hardcode threshold/form/approver?** Karena brief
   menyatakan mereka menjalankan ulang dengan mailbox, key, dan
   invoice mereka sendiri — semua yang bisa berubah ada di
   Config.xlsx / .env.
4. **Kenapa email ditandai dibaca setelah lampiran tersimpan?** Supaya
   tidak ada invoice yang hilang kalau script mati di tengah proses
   (at-least-once, bukan at-most-once).
5. **Kenapa error satu invoice tidak menghentikan run?** Di production,
   satu invoice rusak tidak boleh menghalangi invoice lain. Error
   dicatat per CorrelationId di ProcessLogs dan file-nya diarsipkan ke
   folder FAILED/REJECTED sesuai penyebabnya.
6. **Bagaimana script ini jalan tanpa ditunggui (orchestrator)?**
   One-shot per run + kode keluar yang jelas (0 selesai, 2 config
   salah), semua state di database — tinggal dijadwalkan (Task
   Scheduler / platform orkestrasi) dan dijalankan berkala.

## Kasus tepi yang sudah diverifikasi (8 Nov)

1. **Regex tahan teks OCR berantakan.** Pola di `extractor.py`
   case-insensitive, Amount membuang pemisah ribuan (`Rp 2.750.000` →
   2750000), Currency default IDR, field yang tidak ketemu = None.
   Sudah dicoba langsung ke fungsi aslinya dengan teks berantakan
   (label huruf kecil, spasi ganda) — semua field tetap terbaca.
2. **Satu email berisi 2 gambar = 2 invoice terpisah.**
   `email_intake.py` menelusuri semua bagian email (`msg.walk()`) dan
   menyimpan setiap gambar (.jpg/.jpeg/.png, lampiran maupun inline).
   Masing-masing diproses independen di `main.py` dengan CorrelationId
   sendiri. Nama file sama diganti otomatis (`image_1.jpg`); nomor
   invoice yang sama terkirim dua kali → yang kedua SKIPPED lewat
   `find_by_invoice_number`.
3. **Batasan yang jujur — satu gambar = satu invoice.** Kalau satu
   invoice difoto jadi 2 halaman terpisah, program membacanya sebagai
   2 invoice; menggabungkan halaman belum didukung, karena brief
   memodelkan satu gambar per invoice.
4. **Yang masih bisa ditingkatkan (contoh jawaban bagus):**
   InvoiceNumber tidak di-uppercase — baru PO_Number yang dirapikan.
   Dampaknya bukan cuma tampilan: cek duplikat di database memakai
   pencocokan persis (`WHERE InvoiceNumber = ?`), jadi invoice yang
   dikirim ulang dengan nomor huruf kecil tidak akan ketahuan duplikat.
   Perbaikannya satu baris (`.upper()` saat extract) — sekaligus
   merapikan penyimpanan dan cek duplikat. Sengaja belum diubah
   sebelum run final terekam, supaya kode yang direkam sama dengan
   yang sudah diverifikasi.

## Yang harus bisa kamu ubah langsung saat interview

- Menambah pola regex baru di `extractor.py` (mis. format "Inv No:").
- Mengganti threshold → tunjukkan itu dari Config.xlsx, bukan kode.
- Menambah status validasi baru di `po_validation.py`.
- Menjelaskan arti satu baris di ProcessLogs dari CorrelationId-nya.
- Menunjukkan di mana PostingRef dibuat (`main.py`, saat invoice
  disimpan) dan di mana dipakai (`erp_posting.py`, dari database).

## Checklist sebelum submit

- [ ] Buat form ERP (8 pertanyaan jawaban singkat, lihat README).
- [ ] Isi FormsUrl + ApproverEmail asli di Config.xlsx.
- [ ] Buat `.env` dari `.env.example` (Vision API key dari
      `Key & Git.md`, App Password Gmail).
- [ ] Full run sekali dengan 3 sample → cek 3 Excel + folder Processed.
- [ ] Rekam layar satu run penuh (browser terlihat).
- [ ] Buat repo GitHub, push, bagikan ke reviewer `anggaputrarr`.


---

## Cara menambah field baru (mis. DueDate dari invoice asli)

Satu field mengalir lewat pipa yang sama. Contoh menambah **DueDate**:

**1. `extractor.py`** — ambil dari teks OCR (OCR sudah memberi SEMUA teks;
`vision_ocr.py` tidak perlu diubah):

```python
due = re.search(r"Due\s*Date\s*:\s*(.+)", text, re.IGNORECASE)
# di dict return extract(), tambah:
"DueDate": due.group(1).strip() if due else None,
```

Pola regex menyesuaikan label di invoice-nya ("Jatuh Tempo:" -> ganti polanya).
Kalau field tidak ketemu, nilainya None dan tercatat — memang disengaja.

**2. `database.py`** — simpan:

- Tambah `"DueDate"` ke list `INVOICE_COLUMNS`
- Tambah `DueDate TEXT,` ke `CREATE TABLE invoices`
- Database yang SUDAH ada tidak ikut berubah (CREATE TABLE IF NOT EXISTS),
  jadi jalankan sekali: `ALTER TABLE invoices ADD COLUMN DueDate TEXT;`
  (atau hapus `invoices.db` untuk mulai bersih)
- `save_invoice`/`update_invoice` generik mengikuti INVOICE_COLUMNS — tidak diubah

**3. `main.py`** — dua tempat:

- `process_one`: di dict `row`, tambah `"DueDate": data.get("DueDate"),`
- `post_and_finalize`: di dict `values`, tambah `"DueDate": invoice["DueDate"],`
  (hanya bila field ini juga dikirim ke form ERP)

**4. `exporters.py`** — Invoices.xlsx otomatis mengikuti INVOICE_COLUMNS.
Untuk ERP_Dummy.xlsx: tambah `"DueDate"` ke `ERP_COLUMNS` DAN ke query
SELECT baris `posted` (kolomnya disebut eksplisit di sana).

**5. Google Form** — tambah pertanyaan berjudul persis `DueDate`
(erp_posting mencocokkan field form berdasarkan judul/label).

Tidak perlu diubah: `vision_ocr.py` (teks OCR sudah lengkap),
`po_validation.py` (kecuali field baru harus ikut divalidasi),
`approval.py`.

**Uji cepat tanpa email:** taruh invoice-nya di folder `inbox/`, jalankan
`py main.py --source folder`, lalu cek ProcessLogs langkah `extract` dan
Invoices.xlsx. Total edit ±5–10 menit.


---

## Alur program rinci (versi final, 8 Nov)

**Startup (`main`):** baca `.env` (kredensial) -> `load_config` dari
Config.xlsx (threshold, ApproverEmail, FormsUrl) -> `load_purchase_orders`
dari PurchaseOrders.csv -> `init_db`. Konfigurasi rusak = berhenti dengan
kode keluar 2 sebelum apa pun diproses. Tidak ada nilai bisnis yang
di-hardcode.

**Satu siklus (`run_cycle`) — urutannya disengaja:**

1. **Balasan approval dulu** (`handle_approval_decisions`): pindai mailbox
   2 hari terakhir untuk balasan yang CorrelationId-nya masih PENDING ->
   teks balasan dibersihkan dari kutipan email lama -> keputusan APPROVE /
   REJECT. APPROVED -> langsung `post_and_finalize`; REJECTED -> status
   REJECTED + arsip. Dilanjutkan `retry_failed_postings`: invoice
   POST_FAILED yang approval-nya APPROVED/NOT_REQUIRED diposting ulang.
2. **Intake** (`collect_files`): mode email — unduh lampiran gambar
   (attachment maupun inline dari aplikasi HP) ke `inbox/`, lewati berkas
   yang sudah terarsip; mode folder — ambil gambar dari folder yang ditunjuk.
3. **Per gambar** (`process_image`):
   - Buat CorrelationId (uuid 8 karakter) — penanda invoice ini di semua log.
   - **OCR** (`vision_ocr`) -> teks penuh. Gagal -> catat FAILED, arsip, selesai.
   - **Extract** (`extractor`, regex) -> InvoiceNumber, VendorName,
     PO_Number, Amount, Currency. Field tak ketemu = None, tidak ditebak.
   - **Idempotensi:** InvoiceNumber sudah ada di database -> SKIPPED.
   - **Validasi PO** (`po_validation`) terhadap PurchaseOrders.csv ->
     MATCH / PO_NOT_FOUND / VENDOR_MISMATCH / AMOUNT_MISMATCH / INCOMPLETE.
   - **Cek approval:** Amount > threshold (dari Config) -> perlu approval.
   - **Cabang hasil:** bukan MATCH -> simpan REJECTED, arsip ke
     Processed/REJECTED. MATCH -> buat PostingRef `POST-<InvoiceNumber>`;
     perlu approval -> kirim email permintaan (`approval`, subjek berisi
     CorrelationId), status AWAITING_APPROVAL, berhenti di sini untuk run
     ini; tidak perlu -> langsung posting.
4. **Posting** (`post_and_finalize` + `erp_posting`): Selenium membuka
   FormsUrl, mengisi 8 field dicocokkan berdasarkan judul pertanyaan,
   submit, menunggu konfirmasi (teks halaman ATAU URL berubah ke
   formResponse). Sukses -> PostedAt terisi, status POSTED. Gagal ->
   POST_FAILED (dicoba lagi siklus berikutnya) + tangkapan layar debug.
5. **Tunggu opsional** (`--wait-approval N`): cek balasan tiap 30 detik
   sampai tidak ada yang PENDING atau batas waktu habis.
6. **Ekspor selalu di akhir** (`exporters.export_all`): Invoices.xlsx,
   ERP_Dummy.xlsx (hanya yang PostedAt terisi), ProcessLogs.xlsx — semua
   dari database. File yang terkunci (sedang dibuka di Excel) dilewati
   dengan peringatan, bukan crash.

**Mode watch (`--watch N`):** `run_cycle` diulang tiap N menit; error satu
siklus dicatat dan tidak mematikan program; Ctrl+C berhenti bersih dengan
ekspor terakhir.

**Melintang di semua tahap:** setiap langkah dicatat `log_step` dengan
CorrelationId yang sama; gambar selalu berakhir di
`output/Processed/<Status>` mengikuti status akhirnya; database adalah
sumber kebenaran, Excel hanya laporan.

Contoh pada 3 sample: #1 dan #2 MATCH + di atas threshold -> AWAITING_APPROVAL
-> (balasan APPROVE) -> POSTED. #3 PO-999 tidak ada -> PO_NOT_FOUND -> REJECTED.

Catatan 8 Nov: status validasi baru **PO_INACTIVE** — bila IsActive pada PO di PurchaseOrders.csv bernilai FALSE, invoice ditolak sebagai PO_INACTIVE (bukan PO_NOT_FOUND), karena PO-nya dikenal tapi sudah dinonaktifkan. Pemeriksaan duduk di validate() tepat sesudah PO ditemukan, sebelum cek vendor/nominal.

Catatan 8 Nov (pengerasan untuk invoice yang belum pernah dilihat): extractor kini menerima label alternatif (Invoice No., Supplier, Nama Vendor, Nomor PO, Total, Jumlah) dan nominal berdesimal Indonesia/internasional (1.200.000,00 dibaca 1200000 — pemisah terakhir = desimal); validasi menambah status CURRENCY_MISMATCH (mata uang invoice harus sama dengan PO). Semua uji regresi 3 sample tetap lulus.
