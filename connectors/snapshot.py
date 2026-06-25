"""Snapshot der Connector-Ergebnisse → schneller Dashboard-Start.

Die Connectoren (Mail-Scan + KI-Klassifikation, Lexware-Throttle, diverse APIs) brauchen
beim Kaltstart ~40-60 s. Ein Hintergrund-Job (`prefetch.py`, via launchd) rechnet sie
periodisch durch und legt das Ergebnis hier ab; das Dashboard lädt den Snapshot in
Millisekunden statt live zu rechnen.

Format: pickle von `{"ts": <unix>, "results": [ConnectorResult, ...]}`.
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

SNAP = Path(__file__).resolve().parent.parent / "data" / "dashboard_snapshot.pkl"


def save(results) -> None:
    """Ergebnisse atomar speichern (erst .tmp, dann umbenennen → nie halb-geschrieben)."""
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAP.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump({"ts": time.time(), "results": results}, fh)
    tmp.replace(SNAP)


def load(max_age_min: float | None = None):
    """(results, ts) liefern – oder (None, ts/None), wenn nicht vorhanden/zu alt/defekt."""
    if not SNAP.exists():
        return None, None
    try:
        with open(SNAP, "rb") as fh:
            data = pickle.load(fh)
    except Exception:  # noqa: BLE001 – defekter Snapshot → live rechnen
        return None, None
    ts = data.get("ts", 0.0)
    if max_age_min is not None and (time.time() - ts) > max_age_min * 60:
        return None, ts
    return data.get("results"), ts
