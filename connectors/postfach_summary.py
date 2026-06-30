"""KI-Zusammenfassung der Postfach-Aktivität (buchung@) – pro Vorgang/Kontakt.

Fasst die jüngste eingehende Kunden-Mail je Konversation in MAX 3 Sätzen zusammen,
fokussiert auf Stand und vor allem Probleme/offene Aufgaben – damit Matthias auf einen
Blick sieht, wo Handlungsbedarf ist.

Claude (Haiku), Cache je Konversation + letzter Nachricht: unveränderte Threads werden
nicht erneut zusammengefasst (Token-Kosten minimal).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import graph

CACHE_PATH = os.getenv("POSTFACH_SUMMARY_CACHE", "data/postfach_summary_cache.json")
MODEL = "claude-haiku-4-5-20251001"
FOLDERS = ("Posteingang", "Reisebuchungen", "Anfragen")   # eingehende Kunden-Mails
SENT_FOLDER = "Gesendete Elemente"                          # ausgehende Antworten
DAYS = 7
MAX_ITEMS = 80
_SELECT = ("subject,from,toRecipients,receivedDateTime,sentDateTime,"
           "bodyPreview,conversationId")
# Automatische System-/Benachrichtigungs-Absender (kein echter Kunden-Vorgang) – rausfiltern
SKIP_SENDERS = re.compile(r"no-?reply|no_reply|donotreply|mailer-daemon|xmlteam|msc-booking", re.I)


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


def _sender(m: dict) -> str:
    f = (m.get("from") or {}).get("emailAddress") or {}
    return f.get("name") or f.get("address", "")


def _addr(m: dict) -> str:
    return ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")


def _recipient(m: dict) -> tuple[str, str]:
    """(Name, Adresse) des ersten Empfängers – für gesendete Mails."""
    tos = m.get("toRecipients") or []
    ea = (tos[0].get("emailAddress") or {}) if tos else {}
    return ea.get("name") or ea.get("address", ""), ea.get("address", "")


def _summarize(subject: str, who: str, preview: str, outgoing: bool = False) -> dict:
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if outgoing:
        prompt = (
            "Hier ist eine GESENDETE Antwort eines Kreuzfahrt-Reisebüros an einen Kunden. "
            "Fasse in MAXIMAL 2 kurzen deutschen Sätzen zusammen, was dem Kunden mitgeteilt "
            "oder zugesagt wurde. Antworte NUR als JSON, ohne weiteren Text:\n"
            '{"zusammenfassung":"<max. 2 Sätze>"}\n'
            "Der Text kann technisch abgeschnitten sein – erwähne das NICHT.\n\n"
            f"Empfänger: {who}\nBetreff: {subject}\nText: {preview[:1500]}"
        )
    else:
        prompt = (
            "Hier ist die jüngste E-Mail aus dem Buchungspostfach eines Kreuzfahrt-Reisebüros. "
            "Fasse den Vorgang in MAXIMAL 3 kurzen deutschen Sätzen zusammen – Fokus auf Stand "
            "und vor allem Probleme oder offene Aufgaben. Antworte NUR als JSON, ohne weiteren Text:\n"
            '{"zusammenfassung":"<max. 3 Sätze>","problem":true|false}\n'
            "problem=true nur bei echtem Handlungsbedarf (Beschwerde, Zahlungsproblem, Stornowunsch, "
            "dringende/offene Frage, Fehler). Sonst false.\n"
            "Der Text kann technisch abgeschnitten sein – erwähne das NICHT, fasse nur den "
            "erkennbaren Inhalt zusammen.\n\n"
            f"Absender: {who}\nBetreff: {subject}\nText: {preview[:1500]}"
        )
    r = client.messages.create(model=MODEL, max_tokens=200, temperature=0,
                               messages=[{"role": "user", "content": prompt}])
    text, problem = preview[:160], False
    m = re.search(r"\{.*\}", r.content[0].text, re.S)
    if m:
        try:
            d = json.loads(m.group())
            text = str(d.get("zusammenfassung", text)).strip()
            problem = bool(d.get("problem", False)) and not outgoing
        except (ValueError, TypeError):
            pass
    return {"zusammenfassung": text, "problem": problem}


def summaries() -> list[dict] | None:
    """Liste {kontakt, betreff, text, problem, direction, date} der letzten 7 Tage.

    Eingehende Kunden-Mails (FOLDERS) + ausgehende Antworten (SENT_FOLDER), je
    (Konversation, Tag, Richtung) der jüngste Eintrag. System-Absender raus.
    Der Aufrufer filtert nach Tag.
    """
    if not graph.configured():
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    by_key: dict[tuple, tuple] = {}   # (cid, tag, richtung) -> (msg, richtung)

    def _collect(folder: str, direction: str) -> None:
        for m in graph.messages(folder, top=80, select=_SELECT):
            rdt = m.get("receivedDateTime")
            if not rdt:
                continue
            if datetime.fromisoformat(rdt.replace("Z", "+00:00")) < cutoff:
                break  # Liste ist datum-absteigend
            if direction == "in" and SKIP_SENDERS.search(_addr(m)):
                continue  # automatische System-/No-Reply-Mail -> raus
            if direction == "out" and SKIP_SENDERS.search(_recipient(m)[1]):
                continue
            stamp = (m.get("sentDateTime") if direction == "out" else rdt) or rdt
            day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date().isoformat()
            k = (m.get("conversationId") or m["id"], day, direction)
            cur = by_key.get(k)
            if cur is None or rdt > cur[0]["receivedDateTime"]:
                by_key[k] = (m, direction)

    for folder in FOLDERS:
        _collect(folder, "in")
    _collect(SENT_FOLDER, "out")

    chosen = sorted(by_key.values(), key=lambda md: md[0]["receivedDateTime"], reverse=True)[:MAX_ITEMS]
    cache = _load_cache()
    changed = False
    out: list[dict] = []
    for m, direction in chosen:
        cid = m.get("conversationId") or m["id"]
        # Eingehende Cache-Keys bleiben wie bisher (Bestand erhalten); ausgehende mit ":out"
        key = f"{cid}:{m['receivedDateTime']}" + (":out" if direction == "out" else "")
        who = _recipient(m)[0] if direction == "out" else _sender(m)
        if key in cache:
            info = cache[key]
        else:
            try:
                info = _summarize(m.get("subject", ""), who, m.get("bodyPreview", ""),
                                  outgoing=(direction == "out"))
            except Exception:  # noqa: BLE001
                info = {"zusammenfassung": (m.get("bodyPreview", "") or "")[:150], "problem": False}
            cache[key] = info
            changed = True
        stamp = (m.get("sentDateTime") if direction == "out" else m["receivedDateTime"]) or m["receivedDateTime"]
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
        out.append({"kontakt": who, "betreff": m.get("subject", ""),
                    "text": info.get("zusammenfassung", ""),
                    "problem": bool(info.get("problem")) and direction == "in",
                    "direction": direction, "date": when.date().isoformat(),
                    "cid": cid})   # Konversation: bündelt eingehend + gesendete Antwort

    if changed:
        _save_cache(cache)
    # je Tag: Probleme oben, dann eingehend vor gesendet
    out.sort(key=lambda x: (x["date"], x["problem"], x["direction"] == "in"), reverse=True)
    return out
