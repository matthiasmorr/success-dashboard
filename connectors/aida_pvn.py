"""AIDA PVN (easyCustomers) – Affiliate-Einnahmen.

AIDA ist Mitte 2026 von Awin auf sein eigenes Netzwerk „AIDA PVN" (Dienstleister
easyCustomers / easy-m.de) umgezogen. Die alten Awin-Provisionen (awinmid=9144)
laufen darüber nicht mehr – deshalb dieser eigene Connector. Mein-Schiff bleibt
weiter im Awin-Connector.

Doku: https://docs.easy-m.de (Publisher → Daten-API / Statistik API)
Auth: Access-Token im URL-Pfad. WICHTIG: Die API blockt „nackte" Clients –
ein Browser-artiger User-Agent-Header ist Pflicht (sonst HTTP 403).

Endpunkt:
  https://pvn.aida.de/api/<TOKEN>/publisher/<PUB>/get-statistic_subid.json
    ?condition[mode]=mine
    &condition[period][from]=TT.MM.JJJJ&condition[period][to]=TT.MM.JJJJ
    &condition[l:campaigns]=<KAMPAGNE>

Der subid-Report liefert pro subID (= pro Video/Platzierung): clicks, views,
Provision & Umsatz je Status (open/confirmed/canceled). Wir summieren für die
Kachel; die Rohdaten pro subID lassen sich später fürs Video-Ranking nutzen.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import requests

from .base import Category, ConnectorResult, Metric

NAME = "AIDA (PVN)"
CAT = Category.EINNAHMEN
HOST = "https://pvn.aida.de/api"
# Ohne Browser-UA antwortet die API mit 403.
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _euro(x: float) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def configured() -> bool:
    return bool(os.getenv("AIDA_PVN_TOKEN", "").strip())


def _rows(start: date, end: date) -> list[dict]:
    """subID-Statistik für den Zeitraum [start, end]. Wirft bei API-Fehlern."""
    token = os.getenv("AIDA_PVN_TOKEN", "").strip()
    pub_id = os.getenv("AIDA_PVN_PUBLISHER_ID", "1003").strip()
    campaign = os.getenv("AIDA_PVN_CAMPAIGN_ID", "1").strip()
    # URL manuell bauen: die API erwartet die eckigen Klammern literal.
    url = (
        f"{HOST}/{token}/publisher/{pub_id}/get-statistic_subid.json"
        f"?condition[mode]=mine"
        f"&condition[period][from]={start.strftime('%d.%m.%Y')}"
        f"&condition[period][to]={end.strftime('%d.%m.%Y')}"
        f"&condition[l:campaigns]={campaign}"
    )
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"Unerwartete Antwort: {str(rows)[:200]}")
    return rows


def _num(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _prov(rows: list[dict]) -> float:
    """Provisionssumme (confirmed + open ≈ Awins „approved + pending")."""
    return sum(_num(r_, "all_confirmed_provision") + _num(r_, "all_open_provision") for r_ in rows)


def summary() -> dict | None:
    """Provisionen je Erfolgs-Fenster (heute/gestern/7T/30T) + laufender Monat.

    Für Hero-Bänder und Tageseinnahmen-Kachel. AIDA PVN ist ein eigener
    Einnahmestrom (Affiliate) – NICHT die Newsletter-Werbe-Rechnung, die als
    „AIDA" über Lexware fakturiert wird. None ohne Token; wirft bei API-Fehlern
    – der Aufrufer (heute.py) kapselt mit _safe.
    """
    if not configured():
        return None
    today = date.today()
    gestern = today - timedelta(days=1)
    return {
        "today_prov": _prov(_rows(today, today)),
        "yesterday_prov": _prov(_rows(gestern, gestern)),
        "prov_7d": _prov(_rows(today - timedelta(days=6), today)),
        "prov_30d": _prov(_rows(today - timedelta(days=29), today)),
        "month_prov": _prov(_rows(today.replace(day=1), today)),
    }


def fetch() -> ConnectorResult:
    if not configured():
        return ConnectorResult.missing_config(
            NAME, CAT, "AIDA_PVN_TOKEN in .env setzen (Portal → Account → Daten-API)"
        )

    today = date.today()
    month_start = today.replace(day=1)

    try:
        rows = _rows(month_start, today)

        clicks = sum(int(_num(r_, "clicks")) for r_ in rows)
        provision = _prov(rows)
        sales = sum(int(_num(r_, "all_confirmed_count") + _num(r_, "all_open_count")) for r_ in rows)
        # subIDs mit echtem Namen (criterion != 0 = getrackt) für die Caption
        tracked = [r_ for r_ in rows if r_.get("criterion") and r_.get("clicks")]

        return ConnectorResult(
            name=NAME,
            category=CAT,
            metrics=[
                Metric("Klicks (Monat)", clicks, help="AIDA-Affiliate-Klicks im laufenden Monat (alle subIDs)."),
                Metric("Sales (Monat)", sales, help="Bestätigte + offene Sales im laufenden Monat."),
                Metric(
                    f"Provision seit {month_start.strftime('%d.%m.')}",
                    _euro(provision),
                    help="Summe bestätigter + offener Provisionen (confirmed + open) im laufenden Monat.",
                ),
            ],
            caption=f"{len(tracked)} subIDs mit Klicks · Kampagne AIDA Cruises",
        )
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:200] if e.response is not None else ""
        return ConnectorResult.failed(NAME, CAT, f"HTTP {code}: {body}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
