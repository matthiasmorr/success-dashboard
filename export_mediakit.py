"""Live-Zahlen für das Mediakit auf morr.de/koop-mediadaten exportieren.

Schreibt nur die Werte, die das Dashboard live kennt, in die Zahlendatei der
Website (`src/data/mediakit.json` im morr.de-Repo). Alle übrigen Werte
(engagierte Aufrufe, Sehdauer, Podcast, TikTok, Zielgruppe) bleiben unangetastet
und werden von Hand gepflegt.

Quellen:
  YouTube-Abos, Instagram, Facebook  → data/social_history.json (letzter Tag, vom Prefetch)
  Morrletter Empfänger, Öffnungs- und Klickrate  → KIT v4, Mittel der letzten abgeschlossenen
                                       Aussendungen (frische unter 3 Tagen ausgenommen,
                                       weil deren Klicks noch nachlaufen)

Aufruf:  ./venv/bin/python export_mediakit.py [--dry-run]
Danach im morr.de-Repo committen und pushen.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
# Gemeinsame Schlüssel mit morrCRM; die lokale .env hat Vorrang
load_dotenv(Path("~/.config/morr/.env").expanduser())

ZIEL = Path("~/website/morr-astro-starter/src/data/mediakit.json").expanduser()
HISTORY = Path(__file__).resolve().parent / "data" / "social_history.json"
KIT_API = "https://api.kit.com/v4"
AUSSENDUNGEN = 4          # über so viele Aussendungen wird gemittelt
MIN_ALTER = timedelta(days=3)
MAX_HISTORY_ALTER = timedelta(days=3)


def social_stand() -> tuple[dict[str, int], str]:
    """Letzter Tages-Snapshot aus der Social-Historie: ({plattform: zahl}, datum)."""
    hist = json.loads(HISTORY.read_text())
    tag = max(hist)
    if date.today() - date.fromisoformat(tag) > MAX_HISTORY_ALTER:
        raise RuntimeError(f"Social-Historie ist veraltet (letzter Tag {tag}) – erst prefetch.py laufen lassen")
    return hist[tag], tag


def morrletter_stand() -> dict:
    """Empfänger der letzten Aussendung + mittlere Klickrate der letzten Aussendungen."""
    key = os.getenv("KIT_API_KEY", "").strip()
    if not key:
        raise RuntimeError("KIT_API_KEY fehlt")
    h = {"X-Kit-Api-Key": key, "Accept": "application/json"}
    r = requests.get(f"{KIT_API}/broadcasts", params={"per_page": 25}, headers=h, timeout=20)
    r.raise_for_status()
    jetzt = datetime.now(timezone.utc)
    fertig = []
    for b in r.json().get("broadcasts", []):
        if b.get("status") != "completed" or not b.get("send_at"):
            continue
        gesendet = datetime.fromisoformat(b["send_at"].replace("Z", "+00:00"))
        if jetzt - gesendet >= MIN_ALTER:
            fertig.append((gesendet, b))
    fertig.sort(key=lambda x: x[0], reverse=True)
    if not fertig:
        raise RuntimeError("Keine abgeschlossene Aussendung gefunden")

    alle = []
    for _, b in fertig[:AUSSENDUNGEN * 2]:
        s = requests.get(f"{KIT_API}/broadcasts/{b['id']}/stats", headers=h, timeout=20)
        s.raise_for_status()
        st = s.json().get("broadcast", {}).get("stats", {})
        alle.append({"betreff": b.get("subject", ""), "recipients": int(st.get("recipients", 0)),
                     "click_rate": float(st.get("click_rate", 0)),
                     "open_rate": float(st.get("open_rate", 0))})
    # Nur Aussendungen an die ganze Liste – Extra-Ausgaben an Segmente verzerren den Schnitt
    groesste = max(s["recipients"] for s in alle)
    stats = [s for s in alle if s["recipients"] >= 0.8 * groesste][:AUSSENDUNGEN]
    for s in stats:
        print(f"  · {s['betreff'][:50]:<50} {s['recipients']:>6} Empf. · {s['open_rate']:.1f} % Öffn. · {s['click_rate']:.2f} % Klicks")
    return {
        "empfaenger": stats[0]["recipients"],
        "oeffnungsrate_prozent": round(sum(s["open_rate"] for s in stats) / len(stats), 1),
        "klickrate_prozent": round(sum(s["click_rate"] for s in stats) / len(stats), 1),
    }


def main() -> None:
    dry = "--dry-run" in sys.argv
    daten = json.loads(ZIEL.read_text())
    alt = json.dumps(daten, ensure_ascii=False, sort_keys=True)

    social, tag = social_stand()
    print(f"Social-Historie vom {tag}")
    if social.get("youtube"):
        daten["youtube"]["abonnenten"] = social["youtube"]
    if social.get("instagram"):
        daten["social"]["instagram_follower"] = social["instagram"]
    if social.get("facebook"):
        daten["social"]["facebook_follower"] = social["facebook"]
    if social.get("tiktok"):
        daten["social"]["tiktok_follower"] = social["tiktok"]

    print("Morrletter (KIT):")
    daten["morrletter"].update(morrletter_stand())
    daten["stand"] = date.today().isoformat()

    print("\nNicht automatisch – bitte von Hand prüfen:")
    print(f"  YouTube engagierte Aufrufe/Video: {daten['youtube']['engagierte_aufrufe_pro_video']}")
    print(f"  YouTube Ø Sehdauer (Min):         {daten['youtube']['durchschnittliche_sehdauer_min']}")
    print(f"  Podcast Kontakte/Folge, Folgen:   {daten['podcast']['kontakte_pro_folge']}, {daten['podcast']['folgen']}")
    if not social.get("tiktok"):
        print(f"  TikTok-Follower:                  {daten['social']['tiktok_follower']}")
    print(f"  Zielgruppe je Kanal:              Stand {daten['zielgruppe_stand']} (YouTube Studio, Spotify, Meta)")

    neu = json.dumps(daten, ensure_ascii=False, indent=2) + "\n"
    if dry:
        print("\n--dry-run, nichts geschrieben:\n" + neu)
        return
    if json.dumps(daten, ensure_ascii=False, sort_keys=True) == alt:
        print("\nKeine Änderung.")
        return
    ZIEL.write_text(neu)
    print(f"\nGeschrieben: {ZIEL}\nJetzt im morr.de-Repo committen und pushen.")


if __name__ == "__main__":
    main()
