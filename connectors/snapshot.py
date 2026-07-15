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


def merge_with_previous(results):
    """Fehlgeschlagene Connectoren durch ihr letztes OK-Ergebnis ersetzen.

    Läuft ein (Pre-)Fetch offline oder halb (DNS weg, API-Aussetzer), würde der
    neue Snapshot den guten alten überschreiben – das Dashboard zeigt dann nur
    noch Striche. Stattdessen behält jeder Connector seinen letzten guten Stand;
    »nicht konfiguriert« bleibt sichtbar (das ist ein echter Zustand, kein Ausfall).
    """
    prev, _ts = load()
    if not prev:
        return results
    by_name = {}
    for r in prev:
        name = getattr(r, "name", None)
        if name:
            by_name[name] = r
    merged = []
    for r in results:
        old = by_name.get(getattr(r, "name", None))
        failed = not getattr(r, "ok", True) and getattr(r, "configured", True)
        merged.append(old if (failed and old is not None and getattr(old, "ok", False)) else r)
    return merged


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
