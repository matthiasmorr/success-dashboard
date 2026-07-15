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
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
DEST = Path(__file__).resolve().parent.parent / "data" / "festbuchungen.xlsx"

# Name der privaten Snapshot-Datei in Drive (Cloud-Vorladen, s.u.)
SNAPSHOT_NAME = "dashboard_snapshot.pkl"


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


# ── Cloud-Vorladen: fertigen Snapshot privat in Drive ablegen/holen ──────────
# Die GitHub-Action rechnet prefetch.py durch und lädt den Snapshot hoch; die
# Cloud-App holt ihn beim Start, statt selbst 3-5 Min live zu rechnen.
# Braucht den vollen `drive`-Scope (Upload) → auth_drive.py neu autorisieren.

def _find_file_id(token: str, name: str) -> str | None:
    """File-ID einer Datei in Drive per Name (neueste, optional in einem Ordner)."""
    folder = os.getenv("SNAPSHOT_DRIVE_FOLDER_ID", "").strip()
    q = f"name='{name}' and trashed=false"
    if folder:
        q += f" and '{folder}' in parents"
    r = requests.get(
        f"{DRIVE_API}/files",
        params={"q": q, "fields": "files(id,modifiedTime)",
                "orderBy": "modifiedTime desc", "pageSize": 1, "spaces": "drive",
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
        headers={"Authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    files = r.json().get("files", [])
    return files[0]["id"] if files else None


def _create_file(token: str, name: str) -> str:
    """Leere Datei in Drive anlegen (Metadaten) → File-ID."""
    body: dict = {"name": name}
    folder = os.getenv("SNAPSHOT_DRIVE_FOLDER_ID", "").strip()
    if folder:
        body["parents"] = [folder]
    r = requests.post(
        f"{DRIVE_API}/files",
        params={"fields": "id", "supportsAllDrives": "true"},
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        json=body, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def upload_snapshot(src: Path | None = None) -> str:
    """Lokalen Snapshot nach Drive hochladen (anlegen oder überschreiben).

    Liefert einen Status-String und wirft nicht – scheitert der Upload, läuft
    der Prefetch trotzdem als Erfolg durch (lokaler Snapshot ist geschrieben).
    """
    if not configured():
        return "Drive nicht konfiguriert (Snapshot-Upload übersprungen)"
    from connectors import snapshot
    src = Path(src) if src else snapshot.SNAP
    if not src.exists():
        return "kein lokaler Snapshot zum Hochladen"
    try:
        token = _access_token()
        fid = _find_file_id(token, SNAPSHOT_NAME) or _create_file(token, SNAPSHOT_NAME)
        data = src.read_bytes()
        r = requests.patch(
            f"{UPLOAD_API}/files/{fid}",
            params={"uploadType": "media", "supportsAllDrives": "true"},
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/octet-stream"},
            data=data, timeout=120)
        r.raise_for_status()
        return f"hochgeladen ({len(data)} Bytes, id={fid})"
    except Exception as e:  # noqa: BLE001
        return f"Fehler beim Snapshot-Upload: {e}"


def download_snapshot(dest: Path | None = None) -> str:
    """Fertigen Snapshot aus Drive holen und lokal ablegen (atomar).

    Für die Cloud-App: gibt es keinen lokalen Snapshot, wird der von der
    GitHub-Action hochgeladene geholt. Wirft nicht (graceful Fallback auf live).
    """
    if not configured():
        return "Drive nicht konfiguriert (Snapshot-Download übersprungen)"
    from connectors import snapshot
    dest = Path(dest) if dest else snapshot.SNAP
    try:
        token = _access_token()
        fid = _find_file_id(token, SNAPSHOT_NAME)
        if not fid:
            return "kein Snapshot in Drive gefunden"
        r = requests.get(
            f"{DRIVE_API}/files/{fid}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers={"Authorization": "Bearer " + token}, timeout=120)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        return f"heruntergeladen ({len(r.content)} Bytes)"
    except Exception as e:  # noqa: BLE001
        return f"Fehler beim Snapshot-Download: {e}"


# ── Zustands-Dateien (data/) zwischen Mac und Cloud synchron halten ───────────
# GitHub-Action und Streamlit-Cloud starten ohne data/ (gitignored, Kundendaten).
# Ohne Sync rechnet die Cloud jedes Mal bei null: kein Vorgangs-Ledger, keine
# Klassifikations-Caches, keine Social-Historie → Optionen/Deltas »flackern«, je
# nachdem, wessen Snapshot zuletzt in Drive lag. Deshalb: vor dem Rechnen den
# Drive-Stand in die lokalen Dateien MERGEN (nicht überschreiben), danach den
# vereinten Stand hochladen – Mac und Cloud konvergieren.

STATE_FILES = ("vorgang_ledger.json", "buchungswert_cache.json",
               "postfach_summary_cache.json", "social_history.json",
               "lexware_income_cache.json")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _merge_state(name: str, local: dict, remote: dict) -> dict:
    if name == "vorgang_ledger.json":
        from connectors import ledger  # lazy – zirkulären Import vermeiden
        return ledger.merge(remote, local)
    if name == "social_history.json":
        # pro Tag die Plattform-Werte vereinen; lokal gewinnt bei Konflikt
        out = {d: dict(v) for d, v in remote.items() if isinstance(v, dict)}
        for d, v in local.items():
            if isinstance(v, dict):
                out.setdefault(d, {}).update(v)
        return out
    out = dict(remote)   # Caches (Hash/ID-Keys): Union, lokal gewinnt
    out.update(local)
    return out


def sync_state_down() -> str:
    """Zustands-Dateien aus Drive holen und in die lokalen mergen. Wirft nicht."""
    if not configured():
        return "Drive nicht konfiguriert (State-Sync übersprungen)"
    import json  # noqa: PLC0415
    try:
        token = _access_token()
    except Exception as e:  # noqa: BLE001
        return f"Fehler beim State-Download: {e}"
    done, errs = [], []
    for name in STATE_FILES:
        try:
            fid = _find_file_id(token, name)
            if not fid:
                continue
            r = requests.get(
                f"{DRIVE_API}/files/{fid}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers={"Authorization": "Bearer " + token}, timeout=60)
            r.raise_for_status()
            remote = json.loads(r.content.decode("utf-8"))
            if not isinstance(remote, dict):
                continue
            dest = DATA_DIR / name
            local: dict = {}
            if dest.exists():
                try:
                    local = json.loads(dest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    local = {}
            merged = _merge_state(name, local if isinstance(local, dict) else {}, remote)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(dest)
            done.append(name)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    out = f"{len(done)}/{len(STATE_FILES)} gemergt"
    return out + (f" · Fehler: {'; '.join(errs)}" if errs else "")


def sync_state_up() -> str:
    """Lokale Zustands-Dateien nach Drive hochladen (anlegen/überschreiben). Wirft nicht."""
    if not configured():
        return "Drive nicht konfiguriert (State-Sync übersprungen)"
    try:
        token = _access_token()
    except Exception as e:  # noqa: BLE001
        return f"Fehler beim State-Upload: {e}"
    done, errs = [], []
    for name in STATE_FILES:
        src = DATA_DIR / name
        if not src.exists():
            continue
        try:
            fid = _find_file_id(token, name) or _create_file(token, name)
            r = requests.patch(
                f"{UPLOAD_API}/files/{fid}",
                params={"uploadType": "media", "supportsAllDrives": "true"},
                headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json"},
                data=src.read_bytes(), timeout=120)
            r.raise_for_status()
            done.append(name)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    out = f"{len(done)} hochgeladen"
    return out + (f" · Fehler: {'; '.join(errs)}" if errs else "")
