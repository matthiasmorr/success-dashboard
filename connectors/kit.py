"""KIT (ConvertKit) v4 API – Newsletter-Wachstum (neu heute + 30 Tage).

Auth: Header  X-Kit-Api-Key: <key>
Endpoint: GET /v4/account/growth_stats?starting=YYYY-MM-DD&ending=YYYY-MM-DD
Antwort (live verifiziert):
  {"stats": {"subscribers": 21752,       # Gesamtstand (Snapshot)
             "new_subscribers": 12,       # Brutto-Zugänge im Zeitraum
             "cancellations": -31,
             "net_new_subscribers": -19}} # netto = neu + cancellations
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import requests

from .base import Category, ConnectorResult, Metric

API = "https://api.kit.com/v4"
NAME = "KIT (Morrletter)"
CAT = Category.VANITY


def _stats(key: str, start: date, end: date) -> dict:
    r = requests.get(
        f"{API}/account/growth_stats",
        params={"starting": start.isoformat(), "ending": end.isoformat()},
        headers={"X-Kit-Api-Key": key, "Accept": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("stats", {})


def fetch() -> ConnectorResult:
    key = os.getenv("KIT_API_KEY", "").strip()
    if not key:
        return ConnectorResult.missing_config(NAME, CAT, "KIT_API_KEY fehlt in .env")

    today = date.today()
    try:
        d = _stats(key, today, today)                       # nur heute
        m = _stats(key, today - timedelta(days=30), today)  # letzte 30 Tage

        total = int(m.get("subscribers", 0))
        new_today, net_today = int(d.get("new_subscribers", 0)), int(d.get("net_new_subscribers", 0))
        new_30, net_30 = int(m.get("new_subscribers", 0)), int(m.get("net_new_subscribers", 0))
        cancel_30 = abs(int(m.get("cancellations", 0)))

        return ConnectorResult(
            name=NAME,
            category=CAT,
            metrics=[
                Metric("Neu heute", new_today, delta=net_today,
                       help="Brutto-Zugänge heute · Delta = netto (nach Abmeldungen)"),
                Metric("Letzte 30 Tage", new_30, delta=net_30,
                       help="Brutto-Zugänge in 30 Tagen · Delta = netto"),
            ],
            caption=f"Gesamt {total:,}".replace(",", ".") + f" · 30 Tage: {new_30} neu, {cancel_30} ab → netto {net_30:+d}",
        )
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
