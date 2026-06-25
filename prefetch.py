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
    # Excel ggf. frisch aus Drive ziehen, bevor die Connectoren sie lesen
    print("Drive:", drive.refresh_festbuchungen(max_age_hours=12))
    results = [fetch() for fetch in ALL_CONNECTORS]
    snapshot.save(results)
    ok = sum(1 for r in results if r.ok)
    print(f"Snapshot gespeichert: {ok}/{len(results)} Connectoren OK in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
