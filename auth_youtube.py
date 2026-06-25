"""Einmaliger YouTube-OAuth-Consent → Refresh-Token in .env.

Voraussetzung (Google Cloud Console, gleiches Projekt wie der YOUTUBE_API_KEY):
  1. „YouTube Analytics API" aktivieren.
  2. OAuth-Consent-Screen: External, Scope yt-analytics-monetary.readonly,
     dich als Test-User eintragen, Publishing-Status auf „In Produktion" setzen
     (sonst läuft der Refresh-Token nach 7 Tagen ab).
  3. Anmeldedaten → OAuth-Client-ID (Typ „Desktop") anlegen → Client-ID + Secret.

Aufruf:  ./venv/bin/python auth_youtube.py
(öffnet den Browser; nach deiner Bestätigung wird der Token gespeichert)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/yt-analytics-monetary.readonly"]
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
    cid = os.getenv("YT_OAUTH_CLIENT_ID", "").strip() or input("OAuth Client-ID: ").strip()
    secret = os.getenv("YT_OAUTH_CLIENT_SECRET", "").strip() or input("OAuth Client-Secret: ").strip()
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
                 "'In Produktion' setzen und erneut ausfuehren.")
    _upsert_env("YT_OAUTH_CLIENT_ID", cid)
    _upsert_env("YT_OAUTH_CLIENT_SECRET", secret)
    _upsert_env("YT_OAUTH_REFRESH_TOKEN", creds.refresh_token)
    print("✅ Refresh-Token in .env gespeichert. YouTube-Einnahmen sind jetzt verbunden – "
          "Dashboard neu laden.")


if __name__ == "__main__":
    main()
