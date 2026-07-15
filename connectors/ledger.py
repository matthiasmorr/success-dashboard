"""Vorgangs-Ledger: ein Vorgang = eine Zeile über den ganzen Lebenszyklus.

Löst das Doppelzähl-Problem: eine Reise, die erst als Option und später als
Festbuchung bestätigt wird, ist EINE Zeile (Schlüssel = Vorgangsnummer). Der
Status wandert nur vorwärts (option → festbuchung). Realisierte Einnahme wird
GENAU EINMAL erkannt – am Festbuchungs-Datum. Offene Optionen = Pipeline, NICHT
als Einnahme gezählt.

Persistiert in data/vorgang_ledger.json und wächst über die täglichen Läufe.
Speist sich aus der KI-Klassifikation in booking_value.collect().
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from . import booking_value

LEDGER_PATH = os.getenv("VORGANG_LEDGER", "data/vorgang_ledger.json")
_RANK = {"option": 1, "festbuchung": 2}   # nur vorwärts; storno terminal
# Optionen verfallen: nur als „offene Pipeline" zählen, wenn vor <= N Tagen bestätigt
OPTION_VALID_DAYS = int(os.getenv("OPTION_VALID_DAYS", "3"))


def _load() -> dict:
    try:
        with open(LEDGER_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(led: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER_PATH) or ".", exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as fh:
        json.dump(led, fh, ensure_ascii=False, indent=1)


def update(days: int = 40, top: int = 200) -> dict | None:
    """Klassifiziert die letzten `days` Tage und mergt in den persistenten Ledger."""
    data = booking_value.collect(days=days, top=top)
    if data is None:
        return None
    led = _load()
    for vg, c in data.items():
        # Storno: bestehenden Vorgang als storniert markieren (fliegt aus Einnahme/Pipeline)
        if c["art"] == "storno":
            e = led.get(vg)
            if e is not None:
                e["storno_date"] = c["date"]
            continue
        state = "festbuchung" if c["art"] == "buchung" else "option"
        e = led.get(vg)
        if e is None:
            e = {"nachname": c.get("nachname", ""), "label": c.get("label", ""),
                 "value": c["value"], "state": state, "optionsfrist": c.get("optionsfrist") or None,
                 "option_date": None, "buchung_date": None, "storno_date": None}
            led[vg] = e
        # Status nur vorwärts; bei Erreichen/Höherstufung Wert+Label aktualisieren.
        # value 0 = Preis im PDF nicht erkannt -> bekannten Wert NICHT überschreiben
        # (sonst wird aus einer 4.620-€-Option eine 0-€-Buchung).
        if _RANK[state] >= _RANK[e["state"]]:
            e["state"] = state
            if c["value"]:
                e["value"] = c["value"]
            e["nachname"] = c.get("nachname") or e["nachname"]
            e["label"] = c.get("label") or e["label"]
        if state == "option" and c.get("optionsfrist"):
            e["optionsfrist"] = c["optionsfrist"]
        # frühestes Datum je Stufe festhalten
        dk = "buchung_date" if state == "festbuchung" else "option_date"
        if not e[dk] or c["date"] < e[dk]:
            e[dk] = c["date"]
    _save(led)
    return led


def merge(a: dict, b: dict) -> dict:
    """Zwei Ledger-Stände (lokal ↔ Cloud) konfliktfrei vereinen.

    Beide Seiten rechnen unabhängig (Mac-launchd und GitHub-Action) – ohne Merge
    überschreibt der letzte Schreiber den anderen und Vorgänge »flackern«.
    Regeln je Vorgang: höherer Status gewinnt (option < festbuchung), bekannter
    Wert schlägt 0, Datumsfelder = frühestes bekanntes Datum, Storno bleibt.
    """
    out: dict = {}
    for vg in set(a) | set(b):
        ea, eb = a.get(vg), b.get(vg)
        if ea is None or eb is None:
            out[vg] = dict(ea or eb)
            continue
        hi, lo = ((ea, eb) if _RANK.get(ea.get("state"), 1) >= _RANK.get(eb.get("state"), 1)
                  else (eb, ea))
        e = dict(hi)
        if not e.get("value") and lo.get("value"):
            e["value"] = lo["value"]
        for k in ("nachname", "label", "optionsfrist"):
            if not e.get(k) and lo.get(k):
                e[k] = lo[k]
        for k in ("option_date", "buchung_date", "storno_date"):
            ds = [x.get(k) for x in (ea, eb) if x.get(k)]
            if ds:
                e[k] = min(ds)
        out[vg] = e
    return out


def _state_date(e: dict) -> str | None:
    """Datum des aktuellen Status (für Anzeige/Filter)."""
    return e.get("buchung_date") if e["state"] == "festbuchung" else e.get("option_date")


def _is_real_fest(e: dict) -> bool:
    """Echte, zählbare Festbuchung: Status festbuchung, Wert > 0, nicht storniert."""
    return e["state"] == "festbuchung" and not e.get("storno_date") and e.get("value", 0) > 0


def realized_value(led: dict, start_iso: str, end_iso: str) -> float:
    """Summe der Festbuchungs-Werte mit buchung_date im Fenster (Wert > 0, ohne Stornos)."""
    return sum(
        e["value"] for e in led.values()
        if _is_real_fest(e) and e.get("buchung_date") and start_iso <= e["buchung_date"] <= end_iso)


def realized_count(led: dict, start_iso: str, end_iso: str) -> int:
    """Anzahl Festbuchungen mit buchung_date im Fenster (Wert > 0, ohne Stornos)."""
    return sum(
        1 for e in led.values()
        if _is_real_fest(e) and e.get("buchung_date") and start_iso <= e["buchung_date"] <= end_iso)


def summary() -> dict | None:
    """Alle Kennzahlen für den Hero. None, wenn Graph/Klassifikation nicht verfügbar.

    Laufend nur ~12 Tage scannen (schnell) – ältere Stände stehen persistent im Ledger.
    Für einen kompletten Neuaufbau einmal `update(days=40)` aufrufen.
    """
    led = update(days=12)
    if led is None:
        return None
    today = date.today()
    iso = today.isoformat()
    y = (today - timedelta(days=1)).isoformat()
    d7 = (today - timedelta(days=6)).isoformat()
    d30 = (today - timedelta(days=29)).isoformat()
    month = today.replace(day=1).isoformat()   # laufender Monat liegt nach dem Excel-Cutoff -> reine Mails

    # offene Pipeline = Optionen, die noch gültig sind:
    # echte Optionsfrist (gültig-bis >= heute) bevorzugt, sonst Heuristik (<= OPTION_VALID_DAYS alt)
    opt_cutoff = (today - timedelta(days=OPTION_VALID_DAYS)).isoformat()

    def _open(e):
        if e["state"] != "option" or e.get("storno_date"):
            return False
        frist = e.get("optionsfrist")
        if frist and len(frist) >= 10:
            return frist >= iso          # exakt: gültig bis >= heute
        return (e.get("option_date") or "") >= opt_cutoff   # Fallback: Heuristik

    open_opts = [e for e in led.values() if _open(e)]

    items = sorted(
        ({"art": e["state"].replace("festbuchung", "buchung"), "value": e["value"],
          "date": _state_date(e) or "", "nachname": e.get("nachname", ""),
          "label": e.get("label", "")} for e in led.values() if _state_date(e)),
        key=lambda x: (x["date"], x["value"]), reverse=True)

    return {
        "real_heute": realized_value(led, iso, iso),
        "real_gestern": realized_value(led, y, y),
        "real_7d": realized_value(led, d7, iso),
        "real_30d": realized_value(led, d30, iso),
        "festwert_heute": realized_value(led, iso, iso),
        "n_festbuchung_heute": sum(1 for e in led.values()
                                   if _is_real_fest(e) and e.get("buchung_date") == iso),
        "festwert_monat": realized_value(led, month, iso),   # Festbuchungen laufender Monat (Mails)
        "n_festbuchung_monat": sum(1 for e in led.values()
                                   if _is_real_fest(e) and e.get("buchung_date")
                                   and month <= e["buchung_date"] <= iso),
        "n_option_heute": sum(1 for e in led.values()
                              if e["state"] == "option" and e.get("option_date") == iso),
        "option_value_heute": sum(e["value"] for e in led.values()
                                  if e["state"] == "option" and e.get("option_date") == iso),
        "pipeline_value": sum(e["value"] for e in open_opts),
        "pipeline_count": len(open_opts),
        "items": items,
        "_led": led,   # roher Vorgangs-Dict für den Hybrid-Festwert (Excel + Mail)
    }
