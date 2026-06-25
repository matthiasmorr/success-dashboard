"""KIT v4 – Performance der letzten Aussendung (Öffnungs- + Klickrate).

Endpoints (live verifiziert über die KIT-MCP):
  GET /v4/broadcasts?per_page=...        -> Liste (id, subject, status, send_at)
  GET /v4/broadcasts/{id}/stats          -> {"broadcast": {"stats": {...}}}
Stats-Felder: recipients, open_rate, click_rate, total_clicks, emails_opened,
              unsubscribes, unsubscribe_rate
"""
from __future__ import annotations

import os
from datetime import datetime

import requests

from .base import Category, ConnectorResult, Metric

API = "https://api.kit.com/v4"
NAME = "Letzte Aussendung"
CAT = Category.VANITY


def _headers(key: str) -> dict:
    return {"X-Kit-Api-Key": key, "Accept": "application/json"}


def fetch() -> ConnectorResult:
    key = os.getenv("KIT_API_KEY", "").strip()
    if not key:
        return ConnectorResult.missing_config(NAME, CAT, "KIT_API_KEY fehlt in .env")

    try:
        r = requests.get(f"{API}/broadcasts", params={"per_page": 10}, headers=_headers(key), timeout=20)
        r.raise_for_status()
        broadcasts = r.json().get("broadcasts", [])
        sent = next((b for b in broadcasts if b.get("status") == "completed"), None)
        if not sent:
            return ConnectorResult.failed(NAME, CAT, "Keine abgeschlossene Aussendung gefunden")

        s = requests.get(f"{API}/broadcasts/{sent['id']}/stats", headers=_headers(key), timeout=20)
        s.raise_for_status()
        st = s.json().get("broadcast", {}).get("stats", {})

        open_rate = float(st.get("open_rate", 0))
        click_rate = float(st.get("click_rate", 0))
        recipients = int(st.get("recipients", 0))
        opened = int(st.get("emails_opened", 0))
        clicks = int(st.get("total_clicks", 0))
        unsubs = int(st.get("unsubscribes", 0))

        when = ""
        if sent.get("send_at"):
            try:
                when = datetime.fromisoformat(sent["send_at"].replace("Z", "+00:00")).strftime("%d.%m.%Y")
            except ValueError:
                when = sent["send_at"][:10]

        return ConnectorResult(
            name=NAME,
            category=CAT,
            metrics=[
                Metric("Öffnungsrate", f"{open_rate:.1f} %".replace(".", ","),
                       help=f"{opened:,} von {recipients:,} geöffnet".replace(",", ".")),
                Metric("Klickrate", f"{click_rate:.2f} %".replace(".", ","),
                       help=f"{clicks:,} Klicks insgesamt".replace(",", ".")),
            ],
            caption="„" + sent.get("subject", "") + "“ · " + when
                    + f" · {recipients:,} Empfänger · {unsubs} Abmeldungen".replace(",", "."),
        )
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
