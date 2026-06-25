"""YouTube Data API v3 – Abonnenten & Videoaufrufe.

Braucht nur einen API-Key (kein OAuth), weil wir öffentliche Kanal-Statistiken lesen.
Kanal wird über YOUTUBE_CHANNEL_ID (UC...) oder YOUTUBE_CHANNEL_HANDLE (@name) bestimmt.
"""
from __future__ import annotations

import os

import requests

from .base import Category, ConnectorResult, Metric

API = "https://www.googleapis.com/youtube/v3"
NAME = "YouTube"
CAT = Category.VANITY


def _resolve_channel_id(key: str, handle: str) -> str | None:
    """Handle (@name) -> Channel-ID. Liefert None, wenn nicht auffindbar."""
    handle = handle.lstrip("@")
    r = requests.get(
        f"{API}/channels",
        params={"part": "id", "forHandle": handle, "key": key},
        timeout=15,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    return items[0]["id"] if items else None


def channel_stats() -> dict | None:
    """{subs, views, videos} des Kanals – wiederverwendbar (Connector + Hero). None ohne Key."""
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return None
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    handle = os.getenv("YOUTUBE_CHANNEL_HANDLE", "").strip()
    if not channel_id and handle:
        channel_id = _resolve_channel_id(key, handle)
    if not channel_id:
        return None
    r = requests.get(f"{API}/channels",
                     params={"part": "statistics", "id": channel_id, "key": key}, timeout=15)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return None
    s = items[0]["statistics"]
    return {"subs": int(s.get("subscriberCount", 0)),
            "views": int(s.get("viewCount", 0)),
            "videos": int(s.get("videoCount", 0))}


def fetch() -> ConnectorResult:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return ConnectorResult.missing_config(
            NAME, CAT, "YOUTUBE_API_KEY fehlt in .env"
        )

    channel_id = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    handle = os.getenv("YOUTUBE_CHANNEL_HANDLE", "").strip()

    try:
        if not channel_id:
            if not handle:
                return ConnectorResult.missing_config(
                    NAME, CAT, "YOUTUBE_CHANNEL_ID oder YOUTUBE_CHANNEL_HANDLE setzen"
                )
            channel_id = _resolve_channel_id(key, handle)
            if not channel_id:
                return ConnectorResult.failed(
                    NAME, CAT, f"Kanal zum Handle '{handle}' nicht gefunden"
                )

        r = requests.get(
            f"{API}/channels",
            params={"part": "statistics,snippet", "id": channel_id, "key": key},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return ConnectorResult.failed(NAME, CAT, f"Kanal-ID '{channel_id}' liefert keine Daten")

        ch = items[0]
        stats = ch["statistics"]
        title = ch["snippet"]["title"]
        subs = int(stats.get("subscriberCount", 0))
        views = int(stats.get("viewCount", 0))
        videos = int(stats.get("videoCount", 0))

        # Exakte Abozahl + Aufrufe der letzten 30 Tage über die Analytics API
        # (Data API rundet Abos auf 3 sig. Stellen / kennt kein 30-Tage-Fenster);
        # Fallback auf die gerundete Gesamt-Zahl, falls OAuth nicht verfügbar.
        subs_str = f"{subs:,}".replace(",", ".")
        subs_help = "Gerundet (Data API). Exakt via Analytics – OAuth prüfen."
        views_30d_metric = None
        try:
            from . import youtube_revenue
            exact = youtube_revenue.subscribers_exact()
            if exact is not None:
                subs_str = f"{exact:,}".replace(",", ".")
                subs_help = "Exakt via Analytics API (gewonnen − verloren über die Laufzeit)."
            v30 = youtube_revenue.views_last_30d()
            if v30 is not None:
                views_30d_metric = Metric("Videoaufrufe (30 Tage)", f"{v30:,}".replace(",", "."),
                                          help="Aufrufe der letzten 30 Tage (YouTube Analytics).")
        except Exception:  # noqa: BLE001
            pass

        metrics = [
            Metric("Abonnenten", subs_str, help=subs_help),
            Metric("Videoaufrufe (gesamt)", f"{views:,}".replace(",", ".")),
        ]
        if views_30d_metric is not None:
            metrics.append(views_30d_metric)
        metrics.append(Metric("Videos", f"{videos:,}".replace(",", ".")))

        return ConnectorResult(name=f"{NAME} · {title}", category=CAT, metrics=metrics)
    except requests.HTTPError as e:
        return ConnectorResult.failed(NAME, CAT, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
