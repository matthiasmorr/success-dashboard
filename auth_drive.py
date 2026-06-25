"""Einmaliger Google-Drive-OAuth-Consent → DRIVE_OAUTH_REFRESH_TOKEN in .env.

Damit das Dashboard die Festbuchungen-Excel selbst frisch aus Drive zieht
(connectors/drive.py). Nutzt denselben OAuth-Client wie YouTube
(YT_OAUTH_CLIENT_ID/SECRET).

Voraussetzung (Google Cloud Console, gleiches Projekt wie YouTube):
  1. "Google Drive API" aktivieren.
  2. OAuth-Consent-Screen: Scope `.../auth/drive` hinzufügen (voller Drive-Zugriff –
     nötig zum HOCHLADEN des Snapshots beim Cloud-Vorladen; readonly reicht nur zum Lesen).
  3. Mit dem Google-Konto bestätigen, das Zugriff auf den Kreuzfahrtstudio-Ordner hat
     (bei "App nicht verifiziert": Erweitert → Weiter zu … (unsicher)).

Aufruf:  ./venv/bin/python auth_drive.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

# Voller Drive-Scope: liest die Festbuchungen-Excel UND lädt den Snapshot hoch.
SCOPES = ["https://www.googleapis.com/auth/drive"]
ENV = Path(__file__).with_name(".env")


def _upsert_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    load_dotenv(ENV)
    cid = (os.getenv("DRIVE_OAUTH_CLIENT_ID", "").strip()
           or os.getenv("YT_OAUTH_CLIENT_ID", "").strip()
           or input("OAuth Client-ID: ").strip())
    secret = (os.getenv("DRIVE_OAUTH_CLIENT_SECRET", "").strip()
              or os.getenv("YT_OAUTH_CLIENT_SECRET", "").strip()
              or input("OAuth Client-Secret: ").strip())
    if not cid or not secret:
        sys.exit("Client-ID und Secret werden benötigt.")

    cfg = {"installed": {
        "client_id": cid, "client_secret": secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(cfg, scopes=SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        sys.exit("Kein Refresh-Token erhalten. Tipp: OAuth-Consent-Screen auf "
                 "'In Produktion' setzen und erneut ausführen.")
    _upsert_env("DRIVE_OAUTH_REFRESH_TOKEN", creds.refresh_token)
    print("✅ DRIVE_OAUTH_REFRESH_TOKEN in .env gespeichert. Das Dashboard zieht die "
          "Festbuchungen-Excel jetzt automatisch frisch aus Drive.")


if __name__ == "__main__":
    main()
