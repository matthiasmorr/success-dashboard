"""Website-Reiseanfrage (Netlify-Formular) auslesen – rein textbasiert, ohne KI.

Die Anfragen vom morr.de-Formular kommen technisch alle von derselben Relay-Adresse
`formresponses@netlify.com`; die echte Kundenadresse steht im Reply-To, der Name im
Betreff, die Wünsche als Label/Wert-Paare im Rumpf. Beide Auswerter – die CRM-Liste
(`leads`) und die Postfach-Aktivität (`postfach_summary`) – brauchen genau das, also
liegt es hier gemeinsam statt zweimal.
"""
from __future__ import annotations

import re

FORM_SENDER = "formresponses@netlify.com"   # Relay – nie die Identität des Kunden

# Reihenfolge = Reihenfolge im Formular; der Wert eines Feldes ist der Text bis
# zum nächsten bekannten Label.
# `_P` = optionaler Klammerzusatz, der selbst Doppelpunkte enthalten darf
# („Alter der Kinder (bei mehreren: durch Komma trennen):").
_P = r"\s*(?:\([^)]*\))?\s*:"
FORM_LABELS: list[tuple[str, str]] = [
    ("ziel", r"Gew(?:ü|ue)nschtes Reiseziel\s*\*?" + _P),
    # Variante „konkrete Reise": Schiff + Reisetermin statt Ziel + Dauer.
    # `schiff` steht vor `schiffsgroesse`, kollidiert aber nicht: das Label verlangt
    # den Doppelpunkt direkt hinter „Schiff".
    ("schiff", r"Schiff\s*\*?" + _P),
    ("beginn", r"Beginn der Reise\s*:"),
    ("ende", r"Ende der Reise\s*:"),
    ("getraenke", r"Getr(?:ä|ae)nkepaket\s*:"),
    ("dauer", r"Reisedauer\s*\*?" + _P),
    ("budget", r"Gibt es ein Gesamtbudget\?" + _P),
    ("von", r"Fr(?:ü|ue)heste Anreise\s*:"),
    ("bis", r"Sp(?:ä|ae)teste Abreise\s*:"),
    ("reederei", r"Wunschreederei" + _P),
    ("flughafen", r"Abflughafen" + _P),
    ("kabine", r"Kabinenkategorie\s*\*?" + _P),
    ("erwachsene", r"Anzahl Erwachsene\s*\*?" + _P),
    ("kinder", r"Anzahl Kinder" + _P),
    ("kinderalter", r"Alter der Kinder" + _P),
    ("bedarfsanalyse", r"Ergebnisse Bedarfsanalyse\s*:"),
    ("erfahrung", r"Schon Mal Kreuzfahrt Gemacht\s*:"),
    ("gut", r"Bei welcher Reederei hat es dir gut gefallen\?"),
    ("schlecht", r"Bei welcher Reederei hat es dir nicht so gut gefallen\?"),
    ("neue_reedereien", r"Offen F(?:ü|ue)r Neue Reedereien\s*:"),
    # Restliche Felder werden nicht angezeigt, müssen aber als Label bekannt sein –
    # sonst rutscht der halbe Formular-Rumpf in den Wert des Vorgängerfeldes.
    ("w_sprache", r"Wichtig\s*:\s*Deutsche Sprache An Bord\s*:"),
    ("w_inklusive", r"Wichtig\s*:\s*Viel Im Preis Inklusive\s*:"),
    ("w_kinder", r"Wichtig\s*:\s*Angebot F(?:ü|ue)r Kinder\s*:"),
    ("stil", r"Preis Oder Luxus\s*:"),
    ("bordtag", r"Perfekter Tag An Bord\s*:"),
    ("schiffsgroesse", r"Schiffsgr(?:ö|oe)(?:ß|ss)e\s*:"),
    ("anreise", r"Anreise\s*:"),
    ("seetage", r"H(?:ä|ae)fen Oder Seetage\s*:"),
    ("kunde", r"Name\s*\*\s*:"),
    ("telefon", r"Telefon\s*\*?\s*:"),
    ("mail", r"E-?Mail\s*\*\s*:"),
    ("kontaktweg", r"Wie sollen wir dich kontaktieren\?"),
    ("wunsch", r"W(?:ü|ue)nsche, Anmerkungen und Fragen\s*:"),
    ("datenschutz", r"Datenschutzerkl(?:ä|ae)rung Akzeptiert\s*:"),
    ("angebote_mail", r"Reiseangebote Per E[- ]?Mail Gew(?:ü|ue)nscht\s*:"),
]
_FORM_RE = re.compile("|".join(f"(?P<{k}>{p})" for k, p in FORM_LABELS), re.I)
_MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def is_form(addr: str, subject: str) -> bool:
    """Mail stammt aus dem Website-Formular (Relay-Absender oder Formular-Betreff)."""
    return (addr or "").lower() == FORM_SENDER or "reiseanfrage" in (subject or "").lower()


def plain(html_text: str) -> str:
    """HTML → Fließtext."""
    t = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html_text or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">")
          .replace("&lt;", "<").replace("&euro;", "€").replace("&#8364;", "€"))
    return re.sub(r"\s+", " ", t).strip()


def fields(text: str) -> dict:
    """Formularfelder als Dict (leere Felder fliegen raus)."""
    hits = list(_FORM_RE.finditer(text or ""))
    out: dict[str, str] = {}
    for i, m in enumerate(hits):
        key = m.lastgroup
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        val = text[m.end():end].strip(" .;–—-")
        if val and key and key not in out:
            out[key] = val[:160]
    return out


def clean_mail(v: str | None) -> str:
    """Adresse aus einem Formularwert – der HTML→Text-Schritt streut Leerzeichen ein
    („henning_ahrens@ web.de"), was sonst einen zweiten Kontakt derselben Person ergibt."""
    m = _MAIL_RE.search((v or "").replace(" ", ""))
    return m.group(0).lower() if m else ""


# Anzeige-Reihenfolge der Wunsch-Zeile (Feld, Icon) – identisch in CRM und Aktivität
WISH = [("ziel", "🧭"), ("schiff", "🚢"), ("dauer", "⏱️"), ("kabine", "🛏️"),
        ("reederei", "🚢"), ("flughafen", "✈️")]


def wish_line(f: dict) -> str:
    """Eine Zeile „Ziel · Dauer · Kabine · Personen · Zeitraum · Budget"."""
    bits = [f"{icon} {f[key]}" for key, icon in WISH if f.get(key)]
    pers = []
    if f.get("erwachsene"):
        pers.append(f'{f["erwachsene"]} Erw.')
    if f.get("kinder"):
        pers.append(f'{f["kinder"]} Kind' + ("er" if f["kinder"].strip() != "1" else ""))
    if pers:
        bits.append("👥 " + " + ".join(pers))
    # Reisezeitraum: „frühestens/spätestens" (Variante 1) oder fester Termin (Variante 2)
    ab, bis = f.get("von") or f.get("beginn"), f.get("bis") or f.get("ende")
    if ab or bis:
        def _d(s):
            return f"{s[8:10]}.{s[5:7]}.{s[2:4]}" if len(s or "") >= 10 else (s or "")
        bits.append(f'📅 {_d(ab or "")} – {_d(bis or "")}')
    if f.get("budget"):
        bits.append(f'💶 {f["budget"]}')
    return " · ".join(bits)
