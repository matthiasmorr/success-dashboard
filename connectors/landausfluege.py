"""Meine Landausflüge (TripUp GmbH) – Provisions-Connector aus dem matthias@-Postfach.

Zweite Provisions-Quelle neben dem Kreuzfahrtstudio. Jede Buchung löst eine Mail aus:
  Absender: orders@meine-landausfluege.de
  Betreff:  "Neue Kundenbuchung DE…"
Der Verkaufswert steht im Mail-Body als Feld **GESAMT** (Endpreis nach evtl. Rabatt).
WICHTIG: nicht das Feld "Gesamtpreis" parsen – das enthält bei Rabattcode zwei Zahlen
(durchgestrichener Original- + Rabattpreis). "GESAMT" ist immer der finale Betrag.

Stornos/Erstattungen kommen vom selben Absender mit Betreff "… wurde rückerstattet"
und nennen Buchungsnummer + Betrag ("zurückerstattet: … €"). Sie werden netto
gegengerechnet (Geldfluss-Datum = Eingang der Storno-Mail).

Provision = 10 % auf den Netto-Umsatz. Quelle ist live (Mail kommt sofort), daher zeigen
wir – wie vom Nutzer definiert – den Wert von HEUTE und den Ø der letzten 30 Tage.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

from . import graph
from .base import Category, ConnectorResult, Metric
from .digistore import _euro

NAME = "Meine Landausflüge"
CAT = Category.EINNAHMEN

SENDER = "orders@meine-landausfluege.de"
SUBJECT_BOOKING = "Kundenbuchung"     # "Neue Kundenbuchung DE…"
SUBJECT_REFUND = "rückerstattet"      # "Ihre Bestellung … wurde rückerstattet"
RATE = 0.10                      # 10 % Provision auf den Endpreis
WINDOW_DAYS = 30

_AMOUNT_RE = re.compile(r"([\d.]+,\d{2})\s*€")


def _to_float(num: str) -> float:
    """'1.234,56' -> 1234.56 (deutsches Zahlenformat)."""
    return float(num.replace(".", "").replace(",", "."))


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&euro;", "€").replace("&nbsp;", " ")
                .replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<"))
    return re.sub(r"\s+", " ", text).strip()


def _amount_after(text: str, *anchors: str) -> float | None:
    for anchor in anchors:
        idx = text.find(anchor)        # case-sensitive: 'GESAMT' != 'Gesamtpreis'
        if idx != -1:
            m = _AMOUNT_RE.search(text, idx)
            if m:
                return _to_float(m.group(1))
    return None


def _parse(msg: dict) -> tuple[datetime, float] | None:
    """(empfangen_lokal, signierter_umsatz_eur) – Buchung +, Erstattung −, sonst None.

    Netto-Logik nach Geldfluss-Datum: eine Erstattung mindert den Umsatz an dem Tag,
    an dem die Storno-Mail eingeht (Betrag aus 'zurückerstattet: … €').
    """
    subject = msg.get("subject") or ""
    text = _strip_html((msg.get("body") or {}).get("content", ""))
    if SUBJECT_BOOKING in subject:
        sale = _amount_after(text, "GESAMT", "Zwischensumme")     # Endpreis
        sign = 1.0
    elif SUBJECT_REFUND in subject.lower():
        sale = _amount_after(text, "erstattet")                   # Erstattungsbetrag
        sign = -1.0
    else:
        return None
    if sale is None:
        return None
    received = datetime.fromisoformat(msg["receivedDateTime"].replace("Z", "+00:00"))
    return received.astimezone(), sign * sale


def summary() -> dict:
    """Kennzahlen der letzten 30 Tage – wiederverwendbar (Connector + Hero).

    Liefert Provision heute, Ø Provision/Tag, Netto-Umsatz im Fenster und Zähler.
    Wirft bei fehlender Config/Graph-Fehler – Aufrufer kapseln selbst.
    """
    mailbox = os.getenv("MS_GRAPH_MAILBOX_LANDAUSFLUEGE", "matthias@morr.de").strip()
    today = date.today()
    # Serverseitig auf das Fenster (+1 Tag Slack) eingrenzen -> klein & historie-unabhängig.
    since = today - timedelta(days=WINDOW_DAYS + 1)
    msgs = graph.messages_from_sender(mailbox, SENDER, since=since)
    events = [p for p in (_parse(m) for m in msgs) if p]   # signiert: Buchung +, Storno −

    now_ts = datetime.now(timezone.utc).astimezone().timestamp()
    cutoff = now_ts - WINDOW_DAYS * 86400
    win = [(d, s) for d, s in events if d.timestamp() >= cutoff]
    win_net = sum(s for _, s in win)
    cut7 = now_ts - 7 * 86400
    gestern = today - timedelta(days=1)
    vorgestern = today - timedelta(days=2)
    return {
        "today_prov": sum(s for d, s in events if d.date() == today) * RATE,
        "yesterday_prov": sum(s for d, s in events if d.date() == gestern) * RATE,
        "vorgestern_prov": sum(s for d, s in events if d.date() == vorgestern) * RATE,
        "prov_7d": sum(s for d, s in events if d.timestamp() >= cut7) * RATE,
        "prov_30d": win_net * RATE,
        "avg_prov_day": (win_net * RATE) / WINDOW_DAYS,
        "win_net": win_net,
        "win_prov": win_net * RATE,
        "n_book": sum(1 for _, s in win if s > 0),
        "n_storno": sum(1 for _, s in win if s < 0),
    }


def fetch() -> ConnectorResult:
    if not graph.configured():
        return ConnectorResult.missing_config(
            NAME, CAT, "Microsoft-Graph-Zugang fehlt (MS_GRAPH_* in .env)")
    try:
        s = summary()
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))

    metrics = [
        Metric("Provision heute", _euro(s["today_prov"]),
               help="10 % auf den Netto-Umsatz von heute (Buchungen − Stornos)."),
        Metric(f"Ø Provision/Tag ({WINDOW_DAYS} T.)", _euro(s["avg_prov_day"]),
               help=f"Netto-Provision der letzten {WINDOW_DAYS} Tage ({_euro(s['win_prov'])}) "
                    f"geteilt durch {WINDOW_DAYS}. Vergleichswert für 'heute'."),
    ]
    storno_txt = (f" − {s['n_storno']} Storno{'s' if s['n_storno'] != 1 else ''}"
                  if s["n_storno"] else "")
    cap = (f"Letzte {WINDOW_DAYS} Tage: {s['n_book']} Buchungen{storno_txt} / "
           f"{_euro(s['win_net'])} Netto-Umsatz → {_euro(s['win_prov'])} Provision (10 %)")
    return ConnectorResult(name=NAME, category=CAT, metrics=metrics, caption=cap)
