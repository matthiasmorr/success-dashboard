"""morrCRM als Datenquelle: Buchungen, Provisionsregel und Sales-Leads aus crm.db.

Seit 03.09.2026 (Entscheidung Matthias) liest das Dashboard Buchungen, Leads
und Postfach-Zahlen aus der CRM-Datenbank statt aus eigenen Parsern. Das CRM
liest dieselbe Studio-Excel und dasselbe Postfach, hat aber die reiferen
Regeln (Optionen/Stornos aus dem Excel-Status, Preisformate, alle
Postfach-Ordner, zeitlich verankerte Conversion, Alias-Adressen).

Nur lesend (SQLite im mode=ro). Läuft die Vorberechnung ohne CRM — in der
GitHub-Action gibt es die Datei nicht —, liefert alles hier None, und die
Aufrufer fallen auf ihren bisherigen Weg zurück (Excel aus Drive, Graph).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

CRM_DIR = Path(os.getenv("MORRCRM_DIR", "~/morrCRM")).expanduser()
DB_PATH = CRM_DIR / "crm.db"

KIT_STATE_LABEL = {"active": "aktiv", "inactive": "inaktiv", "cancelled": "abgemeldet",
                   "unsubscribed": "abgemeldet", "bounced": "unzustellbar",
                   "complained": "Beschwerde"}


def verfuegbar() -> bool:
    return DB_PATH.exists()


def _con() -> sqlite3.Connection | None:
    if not verfuegbar():
        return None
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- Provision
def provision_satz(d: date) -> float | None:
    """Satz aus crm/provision.py (die eine Stelle für die Regel); None ohne CRM."""
    if not (CRM_DIR / "crm" / "provision.py").exists():
        return None
    if str(CRM_DIR) not in sys.path:
        sys.path.insert(0, str(CRM_DIR))
    try:
        from crm import provision  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    return provision.satz(d)


# ---------------------------------------------------------------- Buchungen
_STATUS = {"buchung": "OK", "option": "OP", "storniert": "XX"}


def als_dataframe():
    """Studio-Buchungen in der Spaltenform der Excel, damit kreuzfahrtstudio.compute()
    und ok_bookings() unverändert weiterarbeiten. None ohne CRM."""
    con = _con()
    if con is None:
        return None
    import pandas as pd  # noqa: PLC0415
    rows = con.execute(
        """SELECT vorgangs_nr, reise_nr, status, buchungsdatum, preis
             FROM bookings WHERE quelle = 'kreuzfahrtstudio'""").fetchall()
    con.close()
    return pd.DataFrame([{
        "Vorgangs-Nr": r["vorgangs_nr"], "Reise-Nr": r["reise_nr"],
        "Vorgangsstatus": _STATUS.get(r["status"] or "", ""),
        "Buchung": r["buchungsdatum"], "Preis KD": r["preis"],
    } for r in rows])


# ---------------------------------------------------------------- Anfragen
def anfragen(seit: date, bis: date) -> int | None:
    """Website-Anfragen (Formular) mit Eingang im Fenster, aus leads.anfrage_am."""
    con = _con()
    if con is None:
        return None
    n = con.execute(
        """SELECT COUNT(*) FROM leads
            WHERE art = 'lead' AND anfrage = 1
              AND anfrage_am BETWEEN ? AND ?""", (seit.isoformat(), bis.isoformat())).fetchone()[0]
    con.close()
    return n


# ---------------------------------------------------------------- Leads
def leads_summary(days: int = 30) -> dict | None:
    """Sales-Leads in der Struktur von connectors.leads.summary(), aus leads-Tabelle.

    Unterschiede zur alten Postfach-Auswertung: alle Ordner, Cc-Empfänger,
    Alias-Adressen zusammengeführt, Conversion zeitlich ab Erstkontakt und
    nur echte Buchungen (Optionen zählen als Vorgang, nicht als gebucht).
    """
    con = _con()
    if con is None:
        return None
    grenze = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    rows = con.execute(
        """SELECT * FROM leads
            WHERE art = 'lead' AND status != 'ignoriert'
              AND substr(letzter_kontakt, 1, 16) >= ?""", (grenze,)).fetchall()
    con.close()
    today = date.today()
    leads = []
    for r in rows:
        form = json.loads(r["formular"] or "{}")
        letzt = r["letzter_kontakt"] or ""
        offen = bool(r["letzte_richtung"] == "ein" and r["status"] == "offen"
                     and r["ergebnis"] != "ohne_ergebnis")
        vorgang = ("festbuchung" if r["ergebnis"] == "gebucht"
                   else "option" if r["phase"] == "option" else "")
        state = r["kit_state"] or ""
        leads.append({
            "name": r["name"] or r["email"], "email": r["email"],
            "aliasse": json.loads(r["alias_emails"] or "[]") if "alias_emails" in r.keys() else [],
            "n_in": r["anzahl_ein"] or 0, "n_out": r["anzahl_aus"] or 0,
            "erstkontakt": r["erstkontakt"] or letzt[:10],
            "datum": letzt[:10], "zeit": letzt[11:16],
            "tage_still": (today - date.fromisoformat(letzt[:10])).days if letzt else 0,
            "offen": offen, "betreff": r["betreff"] or "",
            "anfrage": bool(r["anfrage"]), "form": form, "budget": float(r["budget"] or 0),
            "telefon": (r["telefon"] or "")[:24],
            "kit_state": state, "kit_since": (r["kit_created_at"] or "")[:10],
            "kit_label": KIT_STATE_LABEL.get(state, state),
            "kit_tags": json.loads(r["kit_tags"] or "[]"),
            "vorgang": vorgang, "vorgang_wert": r["buchung_wert"] or 0,
            "vorgang_label": r["buchung_titel"] or "",
            "kanal": r["kanal"] or "", "phase": r["phase"] or "",
        })
    leads.sort(key=lambda x: (x["offen"] and x["anfrage"], x["offen"], x["datum"], x["zeit"]),
               reverse=True)
    im_kit = [x for x in leads if x["kit_state"]]
    anfragen = [x for x in leads if x["anfrage"]]
    return {
        "leads": leads,
        "n_kontakte": len(leads),
        "n_anfragen": len(anfragen),
        "n_offen": sum(1 for x in leads if x["offen"]),
        "n_kit": len(im_kit),
        "n_kit_aktiv": sum(1 for x in im_kit if x["kit_state"] == "active"),
        "n_nokit": len(leads) - len(im_kit),
        "n_vorgang": sum(1 for x in leads if x["vorgang"]),
        "n_gebucht": sum(1 for x in leads if x["vorgang"] == "festbuchung"),
        "budget_summe": sum(x["budget"] for x in anfragen),
        "conversion": (sum(1 for x in anfragen if x["vorgang"]) / len(anfragen) * 100)
                      if anfragen else 0.0,
        "days": days,
        "quelle": "morrCRM",
    }
