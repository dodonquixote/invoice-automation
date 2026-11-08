"""OCR Google Cloud Vision: gambar invoice -> teks.

Dua jalur autentikasi, dipilih otomatis dari .env:
  1. GOOGLE_VISION_API_KEY  -> REST API (images:annotate?key=...).
     Ini jalur utama karena Protelindo mengirim API key.
  2. GOOGLE_APPLICATION_CREDENTIALS (file JSON service account)
     -> SDK google-cloud-vision, bila jalur 1 tidak diisi.

Kegagalan (key salah, jaringan, kuota) dilempar sebagai VisionError
supaya pemanggil bisa mencatatnya ke ProcessLogs dan LANJUT ke invoice
berikutnya — satu gambar rusak tidak boleh mematikan seluruh proses.
"""
import base64
from pathlib import Path

import requests

from config_loader import env

VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"


class VisionError(Exception):
    pass


def _ocr_via_api_key(image_path, api_key):
    image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    payload = {
        "requests": [{
            "image": {"content": image_b64},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        }]
    }
    try:
        resp = requests.post(
            VISION_ENDPOINT, params={"key": api_key}, json=payload, timeout=60)
    except requests.RequestException as exc:
        raise VisionError(f"jaringan ke Google Vision gagal: {exc}") from exc
    if resp.status_code != 200:
        raise VisionError(
            f"Google Vision HTTP {resp.status_code}: {resp.text[:200]}")
    result = resp.json().get("responses", [{}])[0]
    if "error" in result:
        raise VisionError(f"Google Vision error: {result['error'].get('message')}")
    annotation = result.get("fullTextAnnotation", {})
    return annotation.get("text", "")


def _ocr_via_service_account(image_path):
    try:
        from google.cloud import vision
    except ImportError as exc:
        raise VisionError(
            "google-cloud-vision belum terinstall; install requirements.txt"
            " atau isi GOOGLE_VISION_API_KEY di .env") from exc
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=Path(image_path).read_bytes())
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise VisionError(f"Google Vision error: {response.error.message}")
    return response.full_text_annotation.text


def ocr_image(image_path):
    """Kembalikan teks lengkap hasil OCR satu gambar.

    Raise VisionError bila gagal total (tidak ada kredensial / API error).
    """
    api_key = env("GOOGLE_VISION_API_KEY")
    if api_key:
        return _ocr_via_api_key(image_path, api_key)
    if env("GOOGLE_APPLICATION_CREDENTIALS"):
        return _ocr_via_service_account(image_path)
    raise VisionError(
        "kredensial Vision kosong: isi GOOGLE_VISION_API_KEY di .env")
