"""Validasi invoice terhadap PurchaseOrders.csv.

PurchaseOrders.csv (terverifikasi, 3 baris):
  PO-001, PT Maju Jaya, 1200000, IDR
  PO-002, PT Sukses Makmur, 2500000, IDR
  PO-003, PT Aman Sentosa, 1500000, IDR

Status yang dipakai:
  MATCH            — PO ada dan aktif, vendor dan nominal cocok
  PO_NOT_FOUND     — nomor PO tidak ada di CSV
  PO_INACTIVE      — PO ada tapi IsActive = FALSE (vendor/PO sudah
                     dinonaktifkan di PurchaseOrders.csv)
  VENDOR_MISMATCH  — PO ada tapi vendor beda
  AMOUNT_MISMATCH  — PO ada, vendor cocok, nominal beda
  CURRENCY_MISMATCH — nominal cocok tapi mata uang beda dari PO
  INCOMPLETE       — ada field hasil ekstraksi yang kosong
"""
import csv
from pathlib import Path


def load_purchase_orders(csv_path="PurchaseOrders.csv"):
    orders = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            orders[row["PO_Number"].strip().upper()] = {
                "VendorName": row["VendorName"].strip(),
                "TotalAmount": int(row["TotalAmount"]),
                "Currency": row["Currency"].strip().upper(),
                "IsActive": str(row.get("IsActive", "TRUE")).strip().upper() == "TRUE",
            }
    return orders


def validate(invoice, orders):
    if not invoice.get("InvoiceNumber") or not invoice.get("PO_Number") \
            or invoice.get("Amount") is None or not invoice.get("VendorName"):
        return "INCOMPLETE", "ada field ekstraksi yang kosong"
    po = orders.get(invoice["PO_Number"].upper())
    if po is None:
        return "PO_NOT_FOUND", f"{invoice['PO_Number']} tidak ada di PurchaseOrders.csv"
    if not po["IsActive"]:
        return "PO_INACTIVE", (
            f"{invoice['PO_Number']} ({po['VendorName']}) sudah tidak aktif "
            "(IsActive = FALSE di PurchaseOrders.csv)")
    if po["VendorName"].lower() != invoice["VendorName"].strip().lower():
        return "VENDOR_MISMATCH", (
            f"vendor invoice '{invoice['VendorName']}' != PO '{po['VendorName']}'")
    if po["TotalAmount"] != invoice["Amount"]:
        return "AMOUNT_MISMATCH", (
            f"nominal invoice {invoice['Amount']} != PO {po['TotalAmount']}")
    inv_currency = (invoice.get("Currency") or "IDR").strip().upper()
    if inv_currency != po["Currency"]:
        return "CURRENCY_MISMATCH", (
            f"mata uang invoice {inv_currency} != PO {po['Currency']}")
    return "MATCH", "PO, vendor, nominal, dan mata uang cocok"
