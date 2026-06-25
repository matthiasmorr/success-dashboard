"""Tagesaktueller Buchungswert + Anzahl Buchungen/Optionen (KI-Inhaltsklassifikation).

Statt PDFs per Dateiname zu erkennen (zu brüchig – RCL-Bestätigungen heißen generisch
'document.pdf'), klassifiziert Claude den INHALT jedes Kandidaten-PDFs:
  art ∈ {buchung, option, angebot, storno, sonstiges} · gesamtpreis · vorgang

Kostenkontrolle:
- Namensfilter VORHER (gratis): Boilerplate (AGB, Formulare, …) und reine Angebots-PDFs
  ('7 Nächte ab bis … mit der …') werden gar nicht erst an Claude gegeben.
- Cache per PDF-Inhalts-Hash (data/buchungswert_cache.json) → jedes PDF genau EINMAL an Claude.
- Dedup per Vorgangsnummer; bei mehreren Bestätigungen gewinnt die neueste (Iteration ist datum-absteigend).

Quelle: gesendete Bestätigungen im buchung@-Postfach ('Gesendete Elemente' + 'Reisebuchungen').
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import graph

CACHE_PATH = os.getenv("BUCHUNGSWERT_CACHE", "data/buchungswert_cache.json")
MODEL = "claude-haiku-4-5-20251001"
FOLDERS = ("Gesendete Elemente", "Reisebuchungen")

# Reine Boilerplate-PDFs – zuverlässig benannt, nie ein Buchungswert:
SKIP_PDF = re.compile(
    r"(agb|pauschalreiseform|einreise|formblatt|sicherungsschein|drsf|"
    r"formular|garantiezertifikat|general[_ ]booking|booking[_ ]conditions|"
    r"kreditkart|authoris)", re.I)   # Storno/Cancellation NICHT skippen -> Ledger braucht sie
# Reine Angebots-/Vorschlags-PDFs ('7 Nächte ab bis … mit der …'): kein Vorgang, keine Reservierung
ANGEBOT_PDF = re.compile(r"^\s*\d+\s*N[äa]chte\s+ab\s+bis", re.I)


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


def _vorgang(*texts: str) -> str | None:
    for s in texts:
        m = re.search(r"\b(\d{6,9})\b", s or "")
        if m:
            return m.group(1)
    return None


def _clean_subject(s: str) -> str:
    """Betreff als lesbares Label: Antwort-Präfixe weg, kürzen."""
    s = s or ""
    while True:
        s2 = re.sub(r"^\s*(AW|WG|RE|FWD|FW)\s*:\s*", "", s, flags=re.I)
        if s2 == s:
            break
        s = s2
    return s.strip()[:60]


def _pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader  # noqa: PLC0415

    txt = ""
    for page in PdfReader(io.BytesIO(raw)).pages:
        txt += (page.extract_text() or "") + "\n"
    return txt


def _classify(text: str, subject: str) -> dict:
    """Claude-Inhaltsklassifikation → {art, value, vorgang}."""
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = (
        "Du klassifizierst ein PDF aus einem Kreuzfahrt-Reisebüro. Antworte AUSSCHLIESSLICH "
        "mit einem JSON-Objekt, ohne Markdown, ohne weiteren Text:\n"
        '{"art":"buchung|option|angebot|storno|sonstiges",'
        '"gesamtpreis":<Zahl in EUR, Punkt als Dezimaltrenner, 0 wenn keiner>,'
        '"vorgang":"<Buchungs-/Vorgangs-/Reservierungsnummer oder leerer String>",'
        '"nachname":"<Nachname des/der Hauptreisenden (Kunde), oder leerer String>",'
        '"optionsfrist":"<bei Option: Datum gültig-bis als YYYY-MM-DD, sonst leerer String>",'
        '"reise":"<Schiffsname + kurze Route/Reisebezeichnung, z.B. \\"Silver Nova, Lissabon-Barbados\\", '
        'sonst leerer String>"}\n\n'
        "Definitionen (STRIKT nach Dokumenttitel/-kopf entscheiden):\n"
        "- option: Das Dokument trägt 'Optionsbestätigung', 'Optionsbuchung', 'Option', 'PreContract' "
        "oder 'optioniert' im Titel/Kopf → IMMER 'option', auch wenn es sonst wie eine Bestätigung "
        "aussieht. Eine Option ist unverbindlich/vorläufig reserviert.\n"
        "- buchung: NUR wenn ausdrücklich VERBINDLICH gebucht – 'Buchungsbestätigung', "
        "'Auftragsbestätigung', 'Rechnung', 'verbindliche Buchung', 'Booking Confirmation/Invoice' – "
        "UND das Wort 'Option' NICHT im Titel steht.\n"
        "- angebot: reines Preisangebot/Kabinenvorschlag ohne Reservierung.\n"
        "- storno: Stornorechnung / Stornierung / Cancellation Notice.\n"
        "- sonstiges: AGB, Formulare, Einreisebestimmungen, Sicherungsschein, Kreditkartenformular etc.\n"
        "gesamtpreis = Gesamtreisepreis/Gesamtbetrag der gesamten Buchung in EUR "
        "(nicht Anzahlung, nicht Einzelpreis pro Person).\n"
        "optionsfrist = nur bei Optionen das 'Option gültig bis'/'Optionsfrist'-Datum.\n\n"
        f"Betreff: {subject}\n\nPDF-Text:\n{text[:8000]}"
    )
    # temperature=0 -> deterministische Extraktion (gleiche PDF -> gleicher Betrag)
    r = client.messages.create(model=MODEL, max_tokens=120, temperature=0,
                               messages=[{"role": "user", "content": prompt}])
    raw = r.content[0].text
    art, value, vg, name, frist, reise = "sonstiges", 0.0, "", "", "", ""
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            d = json.loads(m.group())
            art = str(d.get("art", "sonstiges")).strip().lower()
            value = float(str(d.get("gesamtpreis", 0)).replace(",", "."))
            vg = str(d.get("vorgang", "")).strip()
            name = str(d.get("nachname", "")).strip()
            frist = str(d.get("optionsfrist", "")).strip()
            reise = str(d.get("reise", "")).strip()
        except (ValueError, TypeError):
            pass
    return {"art": art, "value": value, "vorgang": vg, "nachname": name,
            "optionsfrist": frist, "reise": reise}


def collect(days: int = 8, top: int = 80) -> dict[str, dict] | None:
    """Vorgangs-Nr -> {art, value, date, nachname, label}. None, wenn Graph nicht konfiguriert."""
    if not graph.configured():
        return None
    cache = _load_cache()
    changed = False
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    by_vorgang: dict[str, dict] = {}

    for folder in FOLDERS:
        for m in graph.messages(folder, top=top):
            if not m.get("hasAttachments"):
                continue
            recv = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
            if recv < cutoff:
                break  # Liste ist nach Datum absteigend
            subject = m.get("subject", "")
            for a in graph.attachments(m["id"]):
                name = a.get("name", "")
                if not name.lower().endswith(".pdf"):
                    continue
                if SKIP_PDF.search(name) or ANGEBOT_PDF.search(name):
                    continue  # Boilerplate/Angebot: nicht an Claude
                try:
                    raw = graph.attachment_bytes(m["id"], a["id"])
                except Exception:  # noqa: BLE001
                    continue
                key = hashlib.md5(raw).hexdigest()
                if key in cache:
                    info = cache[key]
                else:
                    try:
                        info = _classify(_pdf_text(raw), subject)
                    except Exception:  # noqa: BLE001
                        info = {"art": "sonstiges", "value": 0.0, "vorgang": "",
                                "nachname": "", "optionsfrist": "", "reise": ""}
                    cache[key] = info
                    changed = True
                if info["art"] not in ("buchung", "option", "storno"):
                    continue
                vg = info.get("vorgang") or _vorgang(name, subject) or key
                if vg in by_vorgang:
                    continue  # neueste Bestätigung gewann bereits (Iteration datum-absteigend)
                by_vorgang[vg] = {"art": info["art"], "value": info["value"],
                                  "date": recv.date().isoformat(),
                                  "nachname": info.get("nachname", ""),
                                  "optionsfrist": info.get("optionsfrist", ""),
                                  # Label aus PDF (Schiff+Route), Betreff nur als Fallback
                                  "label": info.get("reise") or _clean_subject(subject) or vg}

    if changed:
        _save_cache(cache)
    return by_vorgang


def today_summary() -> dict | None:
    """Tageskennzahlen für den Hero. None, wenn Graph nicht konfiguriert.

    Buchungswert = Gesamtwert aller Wert-Bestätigungen (Buchungen + Optionen) des Tages;
    Anzahl Buchungen / Optionen getrennt. Vergleich heute vs. gestern.
    """
    data = collect(days=8)
    if data is None:
        return None
    heute = datetime.now(timezone.utc).date()
    gestern = heute - timedelta(days=1)
    h, g = heute.isoformat(), gestern.isoformat()

    def wert(day: str) -> float:
        return sum(v["value"] for v in data.values() if v["date"] == day)

    # Einzel-Auflistung (neueste zuerst) für die Hero-Liste
    items = sorted(
        ({"art": v["art"], "value": v["value"], "date": v["date"],
          "nachname": v.get("nachname", ""), "label": v.get("label", "")} for v in data.values()),
        key=lambda x: (x["date"], x["value"]), reverse=True)

    return {
        "wert_heute": wert(h),
        "wert_gestern": wert(g),
        "n_buchung_heute": sum(1 for v in data.values() if v["date"] == h and v["art"] == "buchung"),
        "n_option_heute": sum(1 for v in data.values() if v["date"] == h and v["art"] == "option"),
        "items": items,
    }
