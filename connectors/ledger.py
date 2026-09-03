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
import re
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


def _schiff_of(e: dict) -> str:
    """Schiffsname eines Ledger-Eintrags – Altbestände haben kein 'schiff'-Feld,
    dort steckt er im Label vor dem Komma ('Mein Schiff 2, Karibik…')."""
    if e.get("schiff"):
        return e["schiff"]
    label = e.get("label") or ""
    return label.split(",")[0].strip() if "," in label else ""


def _find_entry(led: dict, key: str, c: dict, alias_index: dict[str, str],
                ident_index: dict[str, str], sig_index: dict[str, str]) -> str | None:
    """Ledger-Schlüssel eines bestehenden Eintrags für diesen Vorgang finden – über
    Schlüssel, Vorgangs-Alias, Identität (Nachname+Schiff+Abreise) oder als letzte
    Stufe Nachname+Schiff+exakter Betrag. So finden Option und Festbuchung derselben
    Reise auch dann zusammen, wenn die Reederei ihnen verschiedene Nummern gegeben
    hat oder eine Bestätigung kein Abreisedatum trägt."""
    if key in led:
        return key
    if key in alias_index:
        return alias_index[key]
    for vg in c.get("vorgaenge") or []:
        if vg in led:
            return vg
        if vg in alias_index:
            return alias_index[vg]
    ident = booking_value.identity_key(c.get("nachname"), c.get("schiff"), c.get("abreise"))
    if ident and ident in ident_index:
        return ident_index[ident]
    sig = booking_value.value_sig(c.get("nachname"), c.get("schiff"), c.get("value"))
    if sig and sig in sig_index:
        return sig_index[sig]
    return None


def update(days: int = 40, top: int = 200) -> dict | None:
    """Klassifiziert die letzten `days` Tage und mergt in den persistenten Ledger."""
    data = booking_value.collect(days=days, top=top)
    if data is None:
        return None
    led = _load()
    alias_index: dict[str, str] = {}
    ident_index: dict[str, str] = {}
    sig_index: dict[str, str] = {}
    for k, e in led.items():
        for vg in e.get("vorgaenge") or []:
            alias_index.setdefault(vg, k)
        ident = booking_value.identity_key(e.get("nachname"), _schiff_of(e), e.get("abreise"))
        if ident:
            ident_index.setdefault(ident, k)
        sig = booking_value.value_sig(e.get("nachname"), _schiff_of(e), e.get("value"))
        if sig:
            sig_index.setdefault(sig, k)

    for key, c in data.items():
        k = _find_entry(led, key, c, alias_index, ident_index, sig_index)
        # Storno: bestehenden Vorgang als storniert markieren (fliegt aus Einnahme/Pipeline)
        if c["art"] == "storno":
            if k is not None:
                led[k]["storno_date"] = c["date"]
            continue
        state = "festbuchung" if c["art"] == "buchung" else "option"
        e = led.get(k) if k else None
        if e is None:
            k = key
            e = {"nachname": c.get("nachname", ""), "label": c.get("label", ""),
                 "value": c["value"], "state": state, "optionsfrist": c.get("optionsfrist") or None,
                 "schiff": c.get("schiff", ""), "abreise": c.get("abreise", ""),
                 "vorgaenge": list(c.get("vorgaenge") or ([key] if not key.startswith("mail:") else [])),
                 "option_date": None, "buchung_date": None, "storno_date": None}
            led[k] = e
        # Status nur vorwärts; bei Erreichen/Höherstufung Wert+Label aktualisieren.
        # value 0 = Preis im PDF nicht erkannt -> bekannten Wert NICHT überschreiben
        # (sonst wird aus einer 4.620-€-Option eine 0-€-Buchung).
        if _RANK[state] >= _RANK[e["state"]]:
            e["state"] = state
            if c["value"]:
                e["value"] = c["value"]
            e["nachname"] = c.get("nachname") or e["nachname"]
            e["label"] = c.get("label") or e["label"]
        if not e.get("value") and c.get("value"):
            e["value"] = c["value"]   # bekannter Preis füllt eine 0 immer
        for fld in ("schiff", "abreise"):
            if not e.get(fld) and c.get(fld):
                e[fld] = c[fld]
        vgs = e.setdefault("vorgaenge", [])
        for vg in c.get("vorgaenge") or []:
            if vg not in vgs:
                vgs.append(vg)
            alias_index.setdefault(vg, k)
        ident = booking_value.identity_key(e.get("nachname"), _schiff_of(e), e.get("abreise"))
        if ident:
            ident_index.setdefault(ident, k)
        sig = booking_value.value_sig(e.get("nachname"), _schiff_of(e), e.get("value"))
        if sig:
            sig_index.setdefault(sig, k)
        if state == "option" and c.get("optionsfrist"):
            e["optionsfrist"] = c["optionsfrist"]
        # frühestes Datum je Stufe festhalten
        dk = "buchung_date" if state == "festbuchung" else "option_date"
        if not e[dk] or c["date"] < e[dk]:
            e[dk] = c["date"]
    led = _dedupe(led)   # selbstheilend, falls ein alter Stand Duplikate einschleppte
    _save(led)
    return led


def _merge_pair(ea: dict, eb: dict) -> dict:
    """Zwei Einträge desselben Vorgangs vereinen: höherer Status gewinnt
    (option < festbuchung), bekannter Wert schlägt 0, Datumsfelder = frühestes
    bekanntes Datum, Storno bleibt, Vorgangs-Aliasse werden vereint."""
    hi, lo = ((ea, eb) if _RANK.get(ea.get("state"), 1) >= _RANK.get(eb.get("state"), 1)
              else (eb, ea))
    e = dict(hi)
    if not e.get("value") and lo.get("value"):
        e["value"] = lo["value"]
    for k in ("nachname", "label", "optionsfrist", "schiff", "abreise"):
        if not e.get(k) and lo.get(k):
            e[k] = lo[k]
    for k in ("option_date", "buchung_date", "storno_date"):
        ds = [x.get(k) for x in (ea, eb) if x.get(k)]
        if ds:
            e[k] = min(ds)
    vgs = list(hi.get("vorgaenge") or [])
    vgs += [v for v in (lo.get("vorgaenge") or []) if v not in vgs]
    if vgs:
        e["vorgaenge"] = vgs
    return e


def _canon_key(key: str) -> str:
    """Hash-/Mail-Schlüssel bleiben; echte Vorgangsnummern werden normalisiert."""
    if key.startswith("mail:") or re.fullmatch(r"[0-9a-f]{32}", key):
        return key
    return booking_value.norm_vorgang(key) or key


def _dedupe(led: dict) -> dict:
    """Duplikate im Ledger zusammenführen (gleiche normalisierte Nummer, gleiche
    Identität oder gleiche Wert-Signatur). Selbstheilend: auch wenn ein alter
    Stand (Drive-Sync, alte Cloud-Action) Duplikat-Schlüssel wieder einschleppt,
    kollabieren sie beim nächsten Merge."""
    out: dict = {}
    for key, e in led.items():
        nk = _canon_key(key)
        out[nk] = _merge_pair(out[nk], e) if nk in out else dict(e)
    idx: dict[str, str] = {}
    for key in list(out.keys()):
        e = out[key]
        sigs = [s for s in (
            booking_value.identity_key(e.get("nachname"), _schiff_of(e), e.get("abreise")),
            booking_value.value_sig(e.get("nachname"), _schiff_of(e), e.get("value")),
        ) if s]
        tgt = next((idx[s] for s in sigs if s in idx and idx[s] != key), None)
        if tgt is not None and tgt in out:
            out[tgt] = _merge_pair(out[tgt], e)
            del out[key]
            key = tgt
        for s in sigs:
            idx.setdefault(s, key)
    return out


def merge(a: dict, b: dict) -> dict:
    """Zwei Ledger-Stände (lokal ↔ Cloud) konfliktfrei vereinen.

    Beide Seiten rechnen unabhängig (Mac-launchd und GitHub-Action) – ohne Merge
    überschreibt der letzte Schreiber den anderen und Vorgänge »flackern«.
    Nach der Vereinigung per Schlüssel läuft eine Dedupe-Passe (Nummer/Identität/
    Signatur), damit Altbestände keine Duplikate wieder einschleppen.
    """
    out: dict = {}
    for vg in set(a) | set(b):
        ea, eb = a.get(vg), b.get(vg)
        out[vg] = _merge_pair(ea, eb) if ea is not None and eb is not None else dict(ea or eb)
    return _dedupe(out)


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
        # Offene Optionen, deren Preis in keiner Bestätigung erkennbar war – die
        # Pipeline-Summe untertreibt dann; ehrlich ausweisen statt still 0 zu zählen
        "pipeline_missing": sum(1 for e in open_opts if not e.get("value")),
        "items": items,
    }
