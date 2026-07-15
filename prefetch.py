"""Hintergrund-Prefetch: Connectoren durchrechnen + Snapshot speichern.

Damit der Dashboard-Start sofort da ist (statt ~40-60 s Kaltstart). Per launchd
regelmäßig aufrufen, z.B. alle 20-30 Min tagsüber.

Aufruf:  ./venv/bin/python prefetch.py
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

os.environ["TZ"] = "Europe/Berlin"
if hasattr(time, "tzset"):
    time.tzset()

load_dotenv()

from connectors import ALL_CONNECTORS, drive, snapshot  # noqa: E402


def main() -> None:
    t0 = time.time()
    # Zustands-Dateien (Vorgangs-Ledger, Klassifikations-Caches, Social-Historie)
    # aus Drive in die lokalen mergen: die Cloud startet ohne data/ und würde sonst
    # jedes Mal bei null anfangen – Mac und Cloud liefen auseinander.
    print("Drive-State:", drive.sync_state_down())
    # In der Cloud (frischer Checkout) fehlt der lokale Snapshot: den letzten fertigen
    # aus Drive holen, damit merge_with_previous auch dort eine Fallback-Basis hat.
    if not snapshot.SNAP.exists():
        print("Drive-Snapshot:", drive.download_snapshot())
    # Excel ggf. frisch aus Drive ziehen, bevor die Connectoren sie lesen
    print("Drive:", drive.refresh_festbuchungen(max_age_hours=12))
    # Fehlgeschlagene Connectoren behalten ihren letzten OK-Stand (Offline-Lauf
    # darf den guten Snapshot nicht mit Strichen überschreiben).
    results = snapshot.merge_with_previous([fetch() for fetch in ALL_CONNECTORS])
    snapshot.save(results)
    ok = sum(1 for r in results if r.ok)
    print(f"Snapshot gespeichert: {ok}/{len(results)} Connectoren OK in {time.time() - t0:.1f}s")
    # Cloud-Vorladen: fertigen Snapshot privat in Drive ablegen (für die Cloud-App)
    print("Drive-Upload:", drive.upload_snapshot())
    print("Drive-State-Upload:", drive.sync_state_up())


if __name__ == "__main__":
    main()
