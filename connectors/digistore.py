"""Digistore24 Vendor-API – Einnahmen des laufenden Monats.

Doku: https://dev.digistore24.com/  (Aufrufe über /api/call/<function>/)
Auth: Header  X-DS-API-KEY: <key>  (entspricht "Nur sichere Authentifizierung")
Antwort: {"result": "success", "data": {...}}

Funktion listTransactions liefert ein fertiges `summary.amounts.<WÄHRUNG>`-Aggregat:
  total_amount  = Bruttoumsatz
  earned_amount = tatsächlich verdient (nach Gebühren/USt./Affiliate-Anteil)
  count         = Anzahl Transaktionen
(live verifiziert gegen das echte Konto)
"""
from __future__ import annotations

import os
from datetime import date

import requests

from .base import Category, ConnectorResult, Metric

API = "https://www.digistore24.com/api/call"
NAME = "Digistore24"
CAT = Category.EINNAHMEN


def _euro(v: float) -> str:
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _call(function: str, key: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"{API}/{function}/",
        params=params or {},
        headers={"X-DS-API-KEY": key, "Accept": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("result") != "success":
        raise RuntimeError(payload.get("message") or "Digistore: result != success")
    return payload.get("data", {})


def fetch() -> ConnectorResult:
    key = os.getenv("DIGISTORE_API_KEY", "").strip()
    if not key:
        return ConnectorResult.missing_config(
            NAME, CAT, "DIGISTORE_API_KEY fehlt in .env (Digistore → Einstellungen → API)"
        )

    today = date.today()
    month_start = today.replace(day=1)
    try:
        # page_size=1: wir brauchen nur das summary-Aggregat, nicht die Einzeltransaktionen
        data = _call("listTransactions", key, {
            "from": month_start.isoformat(), "to": today.isoformat(), "page_size": 1,
        })
        summary = data.get("summary", {})
        amounts = summary.get("amounts", {}) or {}
        # Primär EUR; falls Fremdwährungen existieren, im Caption-Hinweis vermerken
        cur = "EUR" if "EUR" in amounts else next(iter(amounts), None)
        bucket = amounts.get(cur, {}) if cur else {}
        earned = float(bucket.get("earned_amount", 0) or 0)
        count = int(summary.get("count", 0) or 0)
        other_cur = [c for c in amounts if c != cur]

        caption = f"{count} Transaktionen seit {month_start.strftime('%d.%m.')}"
        if other_cur:
            caption += f" · zusätzl. Währungen: {', '.join(other_cur)} (nicht summiert)"

        return ConnectorResult(
            name=NAME,
            category=CAT,
            metrics=[
                Metric("Verdient (Monat)", _euro(earned),
                       help="Nach Gebühren, USt. und Affiliate-Anteil (earned_amount)."),
            ],
            caption=caption,
        )
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
