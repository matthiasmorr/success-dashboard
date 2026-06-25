"""YouTube-Werbeeinnahmen über die YouTube Analytics API (OAuth, monetärer Scope).

`estimatedRevenue` ist NICHT tagesaktuell (Daten stabilisieren sich erst nach ~2-3 Tagen),
darum zeigen wir Monat-bis-dato und den Tagesschnitt der letzten 30 abgeschlossenen Tage.

Einmaliger Consent via `auth_youtube.py` → Refresh-Token (`YT_OAUTH_*` in .env).
Zur Laufzeit nur `requests`: Refresh-Token → Access-Token → Analytics-Report.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from statistics import median

import requests

from .base import Category, ConnectorResult, Metric
from .digistore import _euro

NAME = "YouTube-Einnahmen"
CAT = Category.EINNAHMEN
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://youtubeanalytics.googleapis.com/v2/reports"
LAG_DAYS = 3   # YouTube-Umsatzdaten stabilisieren sich nach ~2-3 Tagen


def _cfg() -> tuple[str, str, str]:
    return (os.getenv("YT_OAUTH_CLIENT_ID", "").strip(),
            os.getenv("YT_OAUTH_CLIENT_SECRET", "").strip(),
            os.getenv("YT_OAUTH_REFRESH_TOKEN", "").strip())


def configured() -> bool:
    return all(_cfg())


# Access-Token cachen: pro Seitenaufruf wird der Token sonst mehrfach geholt
# (summary im Hero + subscribers_exact + separater Einnahmen-Connector). Google
# drosselt zu viele Refreshes in kurzer Folge → YouTube fiel dann sporadisch aus.
_token_cache: dict = {"token": None, "exp": 0.0}


def _access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["exp"]:
        return _token_cache["token"]
    cid, secret, refresh = _cfg()
    r = requests.post(TOKEN_URL, data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    # Lebensdauer ~3600 s; mit 120 s Puffer cachen
    _token_cache["exp"] = time.time() + min(int(data.get("expires_in", 3600)), 3600) - 120
    return _token_cache["token"]


def _revenue(token: str, start: date, end: date) -> float:
    """estimatedRevenue (Konto-Währung) für den Zeitraum [start, end]."""
    r = requests.get(API, params={
        "ids": "channel==MINE", "startDate": start.isoformat(),
        "endDate": end.isoformat(), "metrics": "estimatedRevenue"},
        headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("rows") or [[0]]
    return float(rows[0][0] or 0)


def _revenue_series(token: str, start: date, end: date) -> list[float]:
    """Tageswerte estimatedRevenue für [start, end] – für robuste Statistik (Median)."""
    r = requests.get(API, params={
        "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": "day", "metrics": "estimatedRevenue", "sort": "day"},
        headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    return [float(row[1] or 0) for row in r.json().get("rows", [])]


_subs_cache: dict = {"val": None, "exp": 0.0}


def subscribers_exact(token: str | None = None) -> int | None:
    """Exakte Abozahl des eigenen Kanals via Analytics (gewonnen − verloren über die Laufzeit).

    Die Data API rundet `subscriberCount` auf 3 signifikante Stellen (z.B. 127.000); die
    Analytics API liefert die echten Zu-/Abgänge, deren Differenz die exakte Zahl ergibt.
    None ohne OAuth-Config. Ergebnis wird ~10 Min gecacht (Heute-Strip + Reichweite-Tab
    fragen sonst doppelt ab). Token wird wiederverwendet, wenn übergeben.
    """
    if not configured():
        return None
    if _subs_cache["val"] is not None and time.time() < _subs_cache["exp"]:
        return _subs_cache["val"]
    token = token or _access_token()
    r = requests.get(API, params={
        "ids": "channel==MINE", "startDate": "2005-01-01",
        "endDate": date.today().isoformat(),
        "metrics": "subscribersGained,subscribersLost"},
        headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("rows")
    if not rows:
        return None
    gained, lost = rows[0]
    _subs_cache["val"] = int(gained) - int(lost)
    _subs_cache["exp"] = time.time() + 600
    return _subs_cache["val"]


def views_last_30d(token: str | None = None) -> int | None:
    """Videoaufrufe der letzten 30 Tage (YouTube Analytics, channel==MINE). None ohne Config."""
    if not configured():
        return None
    token = token or _access_token()
    end = date.today()
    start = end - timedelta(days=29)
    r = requests.get(API, params={
        "ids": "channel==MINE", "startDate": start.isoformat(),
        "endDate": end.isoformat(), "metrics": "views"},
        headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("rows") or [[0]]
    return int(rows[0][0] or 0)


def summary() -> dict | None:
    """{month, typical_day, avg_day, stand} – wiederverwendbar (Connector + Hero). None ohne Config.

    typical_day = Median der Tageswerte (robust gegen virale Ausreißer) – das ist die
    Zahl, die wir anzeigen/einrechnen. avg_day (Mittelwert) nur als Zusatzinfo.
    """
    if not configured():
        return None
    token = _access_token()
    today = date.today()
    end = today - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=29)
    series = _revenue_series(token, start, end)
    # Voller Vorkalendermonat = der "eine richtige Betrag/Monat". YouTube finalisiert ihn
    # ca. um den 14. des Folgemonats; davor ist er noch vorläufig.
    prev_last = today.replace(day=1) - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    return {
        "month": _revenue(token, today.replace(day=1), today),
        "last_month": _revenue(token, prev_first, prev_last),
        "last_month_first": prev_first,
        "last_month_final": today.day >= 14,   # ab ~14. gilt der Vormonat als finalisiert
        "typical_day": median(series) if series else 0.0,
        "avg_day": (sum(series) / len(series)) if series else 0.0,
        "rev_7d": sum(series[-7:]) if series else 0.0,     # echte letzte 7 abgeschl. Tage
        "rev_30d": sum(series) if series else 0.0,         # echte letzte 30 abgeschl. Tage
        "stand": end,
    }


def fetch() -> ConnectorResult:
    if not configured():
        return ConnectorResult.missing_config(
            NAME, CAT, "YouTube-OAuth fehlt – einmalig './venv/bin/python auth_youtube.py' "
                       "ausführen (YT_OAUTH_* in .env)")
    try:
        s = summary()
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))

    _mon = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
            "August", "September", "Oktober", "November", "Dezember"]
    pm = s["last_month_first"]
    pm_label = f"{_mon[pm.month - 1]} {pm.year}"
    final = s["last_month_final"]
    return ConnectorResult(name=NAME, category=CAT, metrics=[
        Metric("Werbeeinnahmen (Monat bis dato)", _euro(s["month"]),
               help="estimatedRevenue (Netto-Anteil) ab Monatsanfang, AdSense-Konto-Währung. ~2-3 Tage Datenverzug."),
        Metric(f"Letzter Monat ({pm_label})", _euro(s["last_month"]),
               delta="final" if final else "noch vorläufig", delta_color="off",
               help=f"Voller Kalendermonat {pm_label}. YouTube finalisiert den Monat ca. um den 14. "
                    "des Folgemonats – ab dann entspricht dies dem finalen AdSense-Betrag."),
        Metric("Typischer Tag", _euro(s["typical_day"]),
               help=f"Median der letzten 30 Tage (robust gegen virale Ausreißer) bis "
                    f"{s['stand'].strftime('%d.%m.')}. Mittelwert: {_euro(s['avg_day'])}."),
    ], caption=f"YouTube Analytics · Stand ~{s['stand'].strftime('%d.%m.%Y')} (Datenverzug ~{LAG_DAYS} Tage)")
