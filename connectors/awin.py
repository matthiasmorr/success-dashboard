"""Awin Publisher-API – Affiliate-Einnahmen.

Doku: https://wiki.awin.com/index.php/Publisher_API
Auth: Header  Authorization: Bearer <AWIN_API_TOKEN>
Report: GET /publishers/<publisherId>/transactions/?startDate=...&endDate=...&timezone=Europe/Berlin

Status: Skeleton – beim ersten Lauf mit echtem Token/Publisher-ID gegen die
Live-Antwort finalisieren. Bis dahin sauberer Fehler statt Crash.
"""
from __future__ import annotations

import os
from datetime import date

import requests

from .base import Category, ConnectorResult, Metric

API = "https://api.awin.com"
NAME = "Awin"
CAT = Category.EINNAHMEN


def fetch() -> ConnectorResult:
    token = os.getenv("AWIN_API_TOKEN", "").strip()
    pub_id = os.getenv("AWIN_PUBLISHER_ID", "").strip()
    if not token or not pub_id:
        return ConnectorResult.missing_config(
            NAME, CAT, "AWIN_API_TOKEN und AWIN_PUBLISHER_ID in .env setzen"
        )

    today = date.today()
    month_start = today.replace(day=1)
    try:
        r = requests.get(
            f"{API}/publishers/{pub_id}/transactions/",
            params={
                "startDate": f"{month_start.isoformat()}T00:00:00",
                "endDate": f"{today.isoformat()}T23:59:59",
                "timezone": "Europe/Berlin",
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        txns = r.json()
        if not isinstance(txns, list):
            txns = txns.get("data", []) if isinstance(txns, dict) else []

        confirmed = sum(
            float((t.get("commissionAmount") or {}).get("amount", 0) or 0)
            for t in txns
            if t.get("commissionStatus") in ("approved", "pending")
        )

        return ConnectorResult(
            name=NAME,
            category=CAT,
            metrics=[
                Metric("Transaktionen (Monat)", len(txns)),
                Metric(
                    f"Provision seit {month_start.strftime('%d.%m.')}",
                    f"{confirmed:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."),
                    help="Summe approved + pending Provisionen im laufenden Monat.",
                ),
            ],
        )
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
