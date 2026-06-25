"""Wiederkehrende Fest-Einnahmen (Sponsoring/Werbung), z.B. AIDA-Newsletter-Werbung.

Konfiguration als Liste – einfach erweiterbar. Jede Position: fester Betrag in festem
Intervall ab einem Anker-Datum. Vorkommen werden vorwärts UND rückwärts vom Anker
erzeugt, sodass auch zurückliegende Zahltage im 7-/30-Tage-Fenster korrekt zählen.

Kein API/KI – reine Kalenderlogik. Fließt am Zahltag voll in „Erfolg" (echtes Geld,
keine Provision).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

# Neue feste Einnahmen hier ergänzen:
RECURRING = [
    {"name": "AIDA Newsletter", "amount": 750.0,
     "anchor": date(2026, 7, 5), "interval_days": 14},   # alle 2 Wochen, So
]


def _occurrences(defn: dict, start: date, end: date):
    """Vorkommen-Daten einer Position im Fenster [start, end] (inklusive)."""
    anchor, step = defn["anchor"], defn["interval_days"]
    k = math.ceil((start - anchor).days / step)   # erstes Vorkommen >= start
    d = anchor + timedelta(days=k * step)
    while d <= end:
        if d >= start:
            yield d
        d += timedelta(days=step)


def window_income(start: date, end: date) -> float:
    """Summe aller Fest-Einnahmen mit Zahltag im Fenster [start, end]."""
    return sum(defn["amount"] * sum(1 for _ in _occurrences(defn, start, end))
               for defn in RECURRING)


def next_payment(today: date):
    """(name, datum, betrag) der nächsten anstehenden Zahlung, oder None."""
    best = None
    for defn in RECURRING:
        anchor, step = defn["anchor"], defn["interval_days"]
        k = math.ceil((today - anchor).days / step)
        d = anchor + timedelta(days=k * step)
        if d < today:
            d += timedelta(days=step)
        if best is None or d < best[1]:
            best = (defn["name"], d, defn["amount"])
    return best
