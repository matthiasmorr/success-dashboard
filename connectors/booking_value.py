"""Tagesaktueller Buchungswert + Anzahl Buchungen/Optionen (KI-Inhaltsklassifikation).

Statt PDFs per Dateiname zu erkennen (zu brüchig – RCL-Bestätigungen heißen generisch
'document.pdf'), klassifiziert Claude den INHALT jedes Kandidaten-PDFs:
  art ∈ {buchung, option, angebot, storno, sonstiges} · gesamtpreis · vorgang · identität

PDFs gehen NATIV (base64-Document-Block) an Claude – pypdf-Textextraktion scheiterte
an Tabellenlayouts (MSC/TUI), wodurch ~15% der Bestätigungen ohne Preis blieben.
pypdf bleibt nur als Fallback für PDFs, die die API ablehnt.

Vorgangs-Zusammenführung: Reedereien vergeben mehrere Nummern pro Vorgang (TUI
versioniert '4846657/3', RCL nummeriert Option und Buchung verschieden). Deshalb
verbindet zusätzlich zur normalisierten Vorgangsnummer eine IDENTITÄT
(Nachname + Schiff + Abreisedatum) die Bestätigungen desselben Vorgangs.

Kostenkontrolle:
- Namensfilter VORHER (gratis): Boilerplate (AGB, Formulare, …) und reine Angebots-PDFs
  ('7 Nächte ab bis … mit der …') werden gar nicht erst an Claude gegeben.
- Cache per PDF-Inhalts-Hash (data/buchungswert_cache.json) → jedes PDF genau EINMAL an Claude.
  Alte Cache-Einträge ohne 'schiff'-Feld gelten als Miss und werden einmalig nachklassifiziert.
- Dedup per Vorgangsnummer/Identität; bei mehreren Bestätigungen gewinnt die neueste
  (Mails beider Ordner werden gemeinsam datum-absteigend sortiert).

Quelle: gesendete Bestätigungen im buchung@-Postfach ('Gesendete Elemente' + 'Reisebuchungen').
"""
from __future__ import annotations

import base64
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
# Bestätigungen, die NUR im Mail-Text stehen (ohne PDF – z.B. „vielen Dank für Ihre
# Optionsbuchung!"): eng gefasste Marker, damit Rückfragen/Erwähnungen nicht triggern.
TEXT_MARKER = re.compile(r"options?buchung|optionsbest|buchungsbest|auftragsbest", re.I)


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


def norm_vorgang(vg: str | None) -> str:
    """Vorgangsnummer normalisieren: TUI versioniert Bestätigungen als '4846657/3' –
    jede Version ist derselbe Vorgang."""
    vg = re.sub(r"\s+", "", (vg or "")).upper()
    return re.sub(r"/\d{1,2}$", "", vg)


def _norm_name(nachname: str | None) -> str:
    parts = (nachname or "").strip().lower().split()
    return re.sub(r"[^a-zäöüß]", "", parts[-1]) if parts else ""


def _norm_schiff(schiff: str | None) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^a-z0-9äöüß ]", " ", (schiff or "").lower())).strip()


def identity_key(nachname: str | None, schiff: str | None, abreise: str | None) -> str | None:
    """Vorgangs-Identität über Reederei-Nummern hinweg (RCL nummeriert Option und
    Buchung verschieden). Nur wenn alle drei Teile bekannt sind."""
    n, s, a = _norm_name(nachname), _norm_schiff(schiff), (abreise or "").strip()
    if n and s and len(a) >= 10:
        return f"{n}|{s}|{a[:10]}"
    return None


def value_sig(nachname: str | None, schiff: str | None, value: float | None) -> str | None:
    """Schwächere Identität als Fallback, wenn kein Abreisedatum bekannt ist:
    Nachname + Schiff + exakter Betrag. Der Betrag hält Wiederholungsbuchungen
    derselben Route auseinander (gleiche Reise, anderer Preis = anderer Vorgang);
    zwei exakt preisgleiche Buchungen desselben Kunden auf demselben Schiff
    wären EIN Vorgang – das Restrisiko ist klein und billiger als Doppelzählung."""
    n, s = _norm_name(nachname), _norm_schiff(schiff)
    if n and s and value:
        return f"{n}|{s}|{round(float(value), 2)}"
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


def _html_text(html: str) -> str:
    """Mail-Body (HTML) zu klassifizierbarem Text bereinigen."""
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&gt;", ">").replace("&lt;", "<").replace("&euro;", "€"))
    return re.sub(r"\s+", " ", text).strip()


# PDFs oberhalb dieser Größe nicht base64-hochladen (Request-Limit/Kosten) -> pypdf-Text
_MAX_PDF_BYTES = 4 * 1024 * 1024

_PROMPT = (
    "Du klassifizierst ein Dokument aus einem Kreuzfahrt-Reisebüro (PDF-Bestätigung "
    "oder E-Mail an den Kunden). Antworte AUSSCHLIESSLICH "
    "mit einem JSON-Objekt, ohne Markdown, ohne weiteren Text:\n"
    '{"art":"buchung|option|angebot|storno|sonstiges",'
    '"gesamtpreis":<Zahl in EUR, Punkt als Dezimaltrenner, 0 wenn keiner>,'
    '"vorgang":"<Buchungs-/Vorgangs-/Reservierungsnummer oder leerer String>",'
    '"nachname":"<Nachname des/der Hauptreisenden (Kunde), oder leerer String>",'
    '"schiff":"<nur der Schiffsname, z.B. \\"Mein Schiff 2\\", sonst leerer String>",'
    '"abreisedatum":"<Datum des Reisebeginns/der Einschiffung als YYYY-MM-DD, sonst leerer String>",'
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
    "(nicht Anzahlung, nicht Einzelpreis pro Person). Suche GRÜNDLICH: auch "
    "'Reisepreis', 'Gesamtbetrag', 'Rechnungsbetrag', 'Total', 'Endpreis' oder die "
    "Summenzeile einer Preistabelle zählen. Stehen nur Einzelposten (z.B. pro Person "
    "oder Kabine + Zuschläge), addiere sie zum Gesamtpreis. Nur 0, wenn wirklich "
    "nirgends ein Betrag im Dokument steht.\n"
    "vorgang = die Vorgangs-/Buchungsnummer OHNE Versionssuffix ('4846657/3' → '4846657').\n"
    "optionsfrist = nur bei Optionen das 'Option gültig bis'/'Optionsfrist'-Datum.\n"
)

_EMPTY = {"art": "sonstiges", "value": 0.0, "vorgang": "", "nachname": "",
          "schiff": "", "abreisedatum": "", "optionsfrist": "", "reise": ""}


def _classify(subject: str, text: str = "", pdf: bytes | None = None) -> dict:
    """Claude-Inhaltsklassifikation → {art, value, vorgang, schiff, abreisedatum, …}.

    PDFs gehen nativ als Document-Block an die API (Tabellen bleiben lesbar);
    bei Ablehnung (korruptes/zu großes PDF) Fallback auf extrahierten Text.
    """
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if pdf is not None and len(pdf) <= _MAX_PDF_BYTES:
        content = [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf).decode("ascii")}},
            {"type": "text", "text": f"{_PROMPT}\nBetreff: {subject}"},
        ]
    else:
        if pdf is not None and not text:
            text = _pdf_text(pdf)
        content = [{"type": "text",
                    "text": f"{_PROMPT}\nBetreff: {subject}\n\nDokument-Text:\n{text[:8000]}"}]
    # temperature=0 -> deterministische Extraktion (gleiches Dokument -> gleicher Betrag)
    try:
        r = client.messages.create(model=MODEL, max_tokens=200, temperature=0,
                                   messages=[{"role": "user", "content": content}])
    except anthropic.BadRequestError:
        if pdf is None:
            raise
        # PDF von der API abgelehnt -> einmalig mit pypdf-Text erneut versuchen
        text = _pdf_text(pdf)
        content = [{"type": "text",
                    "text": f"{_PROMPT}\nBetreff: {subject}\n\nDokument-Text:\n{text[:8000]}"}]
        r = client.messages.create(model=MODEL, max_tokens=200, temperature=0,
                                   messages=[{"role": "user", "content": content}])
    raw = next((b.text for b in r.content if b.type == "text"), "")
    info = dict(_EMPTY)
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            d = json.loads(m.group())
            info["art"] = str(d.get("art", "sonstiges")).strip().lower()
            info["value"] = float(str(d.get("gesamtpreis", 0)).replace(",", "."))
            info["vorgang"] = str(d.get("vorgang", "")).strip()
            info["nachname"] = str(d.get("nachname", "")).strip()
            info["schiff"] = str(d.get("schiff", "")).strip()
            info["abreisedatum"] = str(d.get("abreisedatum", "")).strip()
            info["optionsfrist"] = str(d.get("optionsfrist", "")).strip()
            info["reise"] = str(d.get("reise", "")).strip()
        except (ValueError, TypeError):
            pass
    return info


def collect(days: int = 8, top: int = 80) -> dict[str, dict] | None:
    """Schlüssel (Vorgangs-Nr/Identität/Hash) -> {art, value, date, nachname, label,
    schiff, abreise, vorgaenge}. None, wenn Graph nicht konfiguriert."""
    if not graph.configured():
        return None
    cache = _load_cache()
    changed = False
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    def _cached_or_classify(key: str, subject: str, *, text: str = "",
                            pdf: bytes | None = None) -> dict:
        nonlocal changed
        # Einträge ohne 'schiff' stammen aus dem alten Format -> nachklassifizieren
        if key in cache and "schiff" in cache[key]:
            return cache[key]
        try:
            info = _classify(subject, text=text, pdf=pdf)
        except Exception:  # noqa: BLE001
            info = dict(_EMPTY)
        cache[key] = info
        changed = True
        return info

    # Mails BEIDER Ordner gemeinsam datum-absteigend sortieren – sonst gewinnt eine
    # ältere Bestätigung aus 'Gesendete Elemente' gegen eine neuere aus 'Reisebuchungen'.
    mails: list[tuple[datetime, dict]] = []
    for folder in FOLDERS:
        for m in graph.messages(folder, top=top,
                                select="subject,receivedDateTime,hasAttachments,bodyPreview"):
            recv = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
            if recv < cutoff:
                break  # Liste ist je Ordner nach Datum absteigend
            mails.append((recv, m))
    mails.sort(key=lambda t: t[0], reverse=True)

    # Klassifizierte Bestätigungen einsammeln (neueste zuerst): (info, vg, key, recv, subject)
    docs: list[tuple[dict, str, str, datetime, str]] = []
    for recv, m in mails:
        subject = m.get("subject", "")
        got_vorgang = False   # hat diese Mail schon einen Treffer über ein PDF geliefert?
        if m.get("hasAttachments"):
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
                info = _cached_or_classify(key, subject, pdf=raw)
                if info["art"] not in ("buchung", "option", "storno"):
                    continue
                got_vorgang = True
                vg = norm_vorgang(info.get("vorgang") or _vorgang(name, subject) or "")
                docs.append((info, vg, key, recv, subject))

        # Bestätigung nur im MAIL-TEXT (kein verwertbares PDF): z.B. „vielen Dank
        # für Ihre Optionsbuchung!" – sonst fehlen diese Vorgänge komplett im Ledger.
        if not got_vorgang and TEXT_MARKER.search(f"{subject} {m.get('bodyPreview', '')}"):
            try:
                text = _html_text(graph.message_body(m["id"]))
            except Exception:  # noqa: BLE001
                text = ""
            if len(text) >= 40:
                key = "mail:" + hashlib.md5(text.encode("utf-8")).hexdigest()
                info = _cached_or_classify(key, subject, text=text)
                if info["art"] in ("buchung", "option", "storno"):
                    vg = norm_vorgang(info.get("vorgang") or _vorgang(subject) or "")
                    docs.append((info, vg, key, recv, subject))

    if changed:
        _save_cache(cache)

    # Gruppieren: Vorgangsnummer ODER Identität (Nachname+Schiff+Abreise) verbindet.
    # Neueste Bestätigung bestimmt art/value/date; ältere füllen nur Lücken
    # (z.B. Preis, wenn die neueste Bestätigung keinen erkennbaren Betrag trug).
    groups: list[dict] = []
    by_vg: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    by_sig: dict[str, dict] = {}
    for info, vg, hashkey, recv, subject in docs:
        ident = identity_key(info.get("nachname"), info.get("schiff"), info.get("abreisedatum"))
        sig = value_sig(info.get("nachname"), info.get("schiff"), info.get("value"))
        g = ((by_vg.get(vg) if vg else None)
             or (by_id.get(ident) if ident else None)
             or (by_sig.get(sig) if sig else None))
        if g is None:
            g = {"art": info["art"], "value": info["value"],
                 "date": recv.date().isoformat(),
                 "nachname": info.get("nachname", ""),
                 "schiff": info.get("schiff", ""),
                 "abreise": info.get("abreisedatum", ""),
                 "optionsfrist": info.get("optionsfrist", ""),
                 # Label aus Dokument (Schiff+Route), Betreff nur als Fallback
                 "label": info.get("reise") or _clean_subject(subject) or vg or hashkey,
                 "vorgaenge": [vg] if vg else [],
                 "_key": vg or ident or hashkey}
            groups.append(g)
        else:
            if not g["value"] and info["value"]:
                g["value"] = info["value"]
            for src, dst in (("nachname", "nachname"), ("schiff", "schiff"),
                             ("abreisedatum", "abreise"), ("optionsfrist", "optionsfrist")):
                if not g[dst] and info.get(src):
                    g[dst] = info[src]
            if vg and vg not in g["vorgaenge"]:
                g["vorgaenge"].append(vg)
        if vg:
            by_vg[vg] = g
        if ident:
            by_id[ident] = g
        if sig:
            by_sig[sig] = g

    return {g.pop("_key"): g for g in groups}


