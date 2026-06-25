"""Lexware Office Public API – schlanker Client (API-Key, kein OAuth).

Die Streamlit-App kann die MCP-Verbindung aus dem Chat NICHT nutzen – sie braucht
einen eigenen API-Schlüssel: Lexware Office → Einstellungen → Öffentliche API →
Schlüssel erzeugen → in .env als LEXWARE_API_KEY.

Basis: https://api.lexware.io/v1 · Auth: Bearer-Token.
"""
from __future__ import annotations

import os
import time

import requests

API = os.getenv("LEXWARE_API_BASE", "https://api.lexware.io/v1").rstrip("/")
_MIN_INTERVAL = 0.55          # Lexware drosselt auf ~2 Anfragen/Sek.
_state = {"last": 0.0}


def _key() -> str:
    return os.getenv("LEXWARE_API_KEY", "").strip()


def configured() -> bool:
    return bool(_key())


def _headers() -> dict:
    return {"Authorization": "Bearer " + _key(), "Accept": "application/json"}


def _get(path: str, params: dict | None = None) -> requests.Response:
    """GET mit Rate-Limit-Drosselung und Retry bei 429."""
    for attempt in range(6):
        wait = _MIN_INTERVAL - (time.time() - _state["last"])
        if wait > 0:
            time.sleep(wait)
        _state["last"] = time.time()
        r = requests.get(f"{API}{path}", params=params, headers=_headers(), timeout=30)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else (1.0 + attempt))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def voucherlist(voucher_type: str, status: str, date_from: str, date_to: str,
                size: int = 250) -> list[dict]:
    """Belege im Zeitraum (voucherDate). Paginiert automatisch durch alle Seiten."""
    out: list[dict] = []
    page = 0
    while True:
        r = _get("/voucherlist",
                 {"voucherType": voucher_type, "voucherStatus": status,
                  "voucherDateFrom": date_from, "voucherDateTo": date_to,
                  "size": size, "page": page})
        d = r.json()
        out.extend(d.get("content", []))
        if d.get("last", True) or page > 40:
            break
        page += 1
    return out


def net_amount(voucher: dict) -> float:
    """Netto-Betrag eines Belegs (Reverse-Charge-Ausländer: netto=brutto; sonst brutto-USt.).

    invoice -> /invoices/{id}.totalPrice.totalNetAmount;
    salesinvoice (Buchungsbeleg) -> /vouchers/{id}: totalGrossAmount - totalTaxAmount.
    Fallback: brutto aus der Liste.
    """
    vid = voucher["id"]
    vt = voucher.get("voucherType")
    try:
        if vt == "invoice":
            r = _get(f"/invoices/{vid}")
            return float((r.json().get("totalPrice") or {}).get("totalNetAmount", 0) or 0)
        r = _get(f"/vouchers/{vid}")
        d = r.json()
        gross = float(d.get("totalGrossAmount", 0) or 0)
        tax = float(d.get("totalTaxAmount", 0) or 0)
        return gross - tax
    except Exception:  # noqa: BLE001
        return float(voucher.get("totalAmount", 0) or 0)
