"""Google Drive: Festbuchungen-Excel automatisch frisch aus Drive ziehen (OAuth, readonly).

Hält `data/festbuchungen.xlsx` aktuell, ohne manuelles Nachladen. Nutzt denselben
OAuth-Client wie YouTube (`YT_OAUTH_CLIENT_ID`/`SECRET`); eigener Refresh-Token
`DRIVE_OAUTH_REFRESH_TOKEN` (einmalig via `auth_drive.py`).

Zur Laufzeit nur `requests`: Refresh-Token → Access-Token → Datei-Download. Mit
Staleness-Guard: lädt nur neu, wenn die lokale Datei älter als `max_age_hours` ist.
Fehlt die Konfiguration, passiert nichts (das Dashboard läuft mit der lokalen Datei weiter).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DEST = Path(__file__).resolve().parent.parent / "data" / "festbuchungen.xlsx"


def _cfg() -> tuple[str, str, str]:
    """Client-ID/Secret (Fallback: YouTube-Client) + Drive-Refresh-Token."""
    cid = (os.getenv("DRIVE_OAUTH_CLIENT_ID", "").strip()
           or os.getenv("YT_OAUTH_CLIENT_ID", "").strip())
    secret = (os.getenv("DRIVE_OAUTH_CLIENT_SECRET", "").strip()
              or os.getenv("YT_OAUTH_CLIENT_SECRET", "").strip())
    refresh = os.getenv("DRIVE_OAUTH_REFRESH_TOKEN", "").strip()
    return cid, secret, refresh


def configured() -> bool:
    return all(_cfg())


def _access_token() -> str:
    cid, secret, refresh = _cfg()
    r = requests.post(TOKEN_URL, data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def download(file_id: str, dest: Path = DEST) -> int:
    """Datei-Inhalt aus Drive laden und nach `dest` schreiben. Liefert Byte-Anzahl."""
    token = _access_token()
    r = requests.get(
        f"{DRIVE_API}/files/{file_id}",
        params={"alt": "media", "supportsAllDrives": "true"},
        headers={"Authorization": "Bearer " + token}, timeout=60)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return len(r.content)


def refresh_festbuchungen(max_age_hours: float = 12, dest: Path = DEST) -> str:
    """Excel aus Drive nachladen, falls die lokale Datei älter als `max_age_hours` ist.

    Liefert einen kurzen Status-String (für Logs/Debug). Wirft nicht – Fehler werden
    abgefangen, damit das Dashboard immer mit der vorhandenen Datei weiterläuft.
    """
    file_id = os.getenv("KREUZFAHRTSTUDIO_FILE_ID", "").strip()
    if not configured() or not file_id:
        return "Drive-Auto-Refresh nicht konfiguriert (DRIVE_OAUTH_REFRESH_TOKEN/FILE_ID fehlt)"
    if dest.exists() and (time.time() - dest.stat().st_mtime) < max_age_hours * 3600:
        return "aktuell – kein Refresh nötig"
    try:
        n = download(file_id, dest)
        return f"aktualisiert ({n} Bytes)"
    except Exception as e:  # noqa: BLE001
        return f"Fehler beim Drive-Refresh: {e}"
