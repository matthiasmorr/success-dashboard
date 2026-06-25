"""Fakturierte Einnahmen aus Lexware (netto) – Ergebnis der letzten 30 Tage.

Nur Quellen OHNE eigenen Live-Connector (sonst Doppelzählung): AIDA, Giesswein, e-hoi,
MSC, Amazon, Meta, Spotify, prepmymeal … AUSGESCHLOSSEN: Google/YouTube, Digistore24,
Awin, TripUp (Landausflüge), Kreuzfahrtstudio – die haben eigene Tages-Connectoren.

Nach Rechnungsdatum (voucherDate), netto pro Beleg (via lexware.net_amount, gecacht).
Lexware lagt: aktueller Monat unvollständig, solange Belege noch nicht angelegt sind
(z.B. Amazon-Proformarechnungen entstehen manuell, Wochen später).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

from . import lexware

CACHE_PATH = os.getenv("LEXWARE_INCOME_CACHE", "data/lexware_income_cache.json")
WINDOW_DAYS = 30
# Meta/Spotify stehen in Lexware fälschlich als EUR, sind real USD. Standard 1.0 = wie in
# Lexware übernehmen (Nutzer rechnet später selbst um); zum Umrechnen LEXWARE_USD_TO_EUR setzen.
USD_SOURCES = {"Meta", "Spotify"}
USD_TO_EUR = float(os.getenv("LEXWARE_USD_TO_EUR", "1.0"))
# Quellen mit eigenem Live-Connector -> hier ausschließen (kein Doppelzählen)
EXCLUDE = re.compile(r"google|digistore|\bawin\b|tripup|kreuzfahrtstudio", re.I)
# Sachwert-/Tausch-Einnahmen (Produkt statt Geld): diese Marken liefern Ware, die als
# salesinvoice-Warenbeleg in Lexware landet -> kein Geldfluss, raus. ECHTE Rechnungen
# (voucherType "invoice", z.B. Giesswein RE-…) bleiben. Plattform-Auszahlungen wie
# Meta/Spotify/e-hoi sind ebenfalls salesinvoice, aber echt -> NICHT in dieser Liste.
# Selten; bei Bedarf Markennamen ergänzen.
SACHEINNAHMEN = re.compile(r"\bjuit\b|giesswein", re.I)
# Kontaktname -> kurzes Label (Mehrfach-Entitäten zusammenfassen)
_LABELS = [
    ("aida", "AIDA"), ("giesswein", "Giesswein"), ("e-hoi", "e-hoi"),
    ("msc", "MSC"), ("amazon", "Amazon"), ("meta", "Meta"), ("spotify", "Spotify"),
    ("prepmymeal", "prepmymeal"), ("juit", "Juit"), ("algotels", "Algotels"),
    ("sprd", "Spreadshirt"),
]


def _label(name: str) -> str:
    low = (name or "").lower()
    for needle, lab in _LABELS:
        if needle in low:
            return lab
    return (name or "Sonstige").split(" – ")[0].split(",")[0].strip()[:22]


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)


def summary() -> dict | None:
    """{total_30d, by_source: {label: netto}, since}. None ohne API-Key."""
    if not lexware.configured():
        return None
    today = date.today()
    since = today - timedelta(days=WINDOW_DAYS)
    vouchers = lexware.voucherlist("invoice,salesinvoice", "any",
                                   since.isoformat(), today.isoformat())
    cache = _load_cache()
    changed = False
    by_source: dict[str, float] = {}
    for v in vouchers:
        name = v.get("contactName", "")
        if EXCLUDE.search(name):
            continue  # hat eigenen Tages-Connector
        if SACHEINNAHMEN.search(name) and v.get("voucherType") == "salesinvoice":
            continue  # Sachwert-Warenbeleg (kein Geldfluss); echte Rechnung bleibt
        key = f"{v['id']}:{v.get('updatedDate', '')}"
        if key in cache:
            net = cache[key]
        else:
            net = lexware.net_amount(v)
            cache[key] = net
            changed = True
        lab = _label(name)
        if lab in USD_SOURCES:
            net *= USD_TO_EUR   # Meta/Spotify real USD (Standard 1.0 = unverändert)
        by_source[lab] = by_source.get(lab, 0.0) + net
    if changed:
        _save_cache(cache)
    by_source = {k: v for k, v in sorted(by_source.items(), key=lambda x: -x[1])}
    return {"total_30d": sum(by_source.values()), "by_source": by_source, "since": since}
