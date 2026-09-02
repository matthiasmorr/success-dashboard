"""KI-Zusammenfassung der Postfach-Aktivität (buchung@) – pro Vorgang/Kontakt.

Fasst die jüngste eingehende Kunden-Mail je Konversation in MAX 3 Sätzen zusammen,
fokussiert auf Stand und vor allem Probleme/offene Aufgaben – damit Matthias auf einen
Blick sieht, wo Handlungsbedarf ist. Zusätzlich extrahiert die KI den KUNDENNAMEN
(Anzeigename ist oft generisch, z.B. 'morr.de' beim Website-Formular) und markiert
ANFRAGEN (= Leads: jemand möchte eine Reise/Beratung, hat aber noch keinen Vorgang).

Claude (Haiku), Cache je Konversation + letzter Nachricht: unveränderte Threads werden
nicht erneut zusammengefasst (Token-Kosten minimal). Grundlage ist der volle Mail-Text
(body, HTML-bereinigt) – bodyPreview (255 Zeichen) war für brauchbare
Zusammenfassungen zu kurz.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import graph, webform

CACHE_PATH = os.getenv("POSTFACH_SUMMARY_CACHE", "data/postfach_summary_cache.json")
MODEL = "claude-haiku-4-5-20251001"
FOLDERS = ("Posteingang", "Reisebuchungen", "Anfragen")   # eingehende Kunden-Mails
SENT_FOLDER = "Gesendete Elemente"                          # ausgehende Antworten
DAYS = 7
MAX_ITEMS = 80
_SELECT = ("subject,from,replyTo,toRecipients,receivedDateTime,sentDateTime,"
           "bodyPreview,body,conversationId")
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


def _body_text(m: dict) -> str:
    """Voller Mail-Text (HTML-bereinigt); Fallback bodyPreview."""
    content = (m.get("body") or {}).get("content", "") or ""
    if content:
        content = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", content, flags=re.S | re.I)
        content = re.sub(r"<[^>]+>", " ", content)
        content = (content.replace("&nbsp;", " ").replace("&amp;", "&")
                          .replace("&gt;", ">").replace("&lt;", "<").replace("&euro;", "€"))
        content = re.sub(r"\s+", " ", content).strip()
    return content or (m.get("bodyPreview", "") or "")


def _generic_name(who: str) -> bool:
    """Anzeigename ist kein echter Personenname (Adresse, Domain, Formular-Absender)."""
    w = (who or "").strip()
    if not w or "@" in w:
        return True
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+", w):   # domain-artig, z.B. 'morr.de'
        return True
    return w.lower() in ("website", "kontaktformular", "kontakt", "info", "buchung")


_ANREDE = ("frau", "herr", "familie", "fam.", "hr.", "fr.")


def _full_name(n: str) -> bool:
    """Echter voller Name (mit Vornamen): ≥2 Wörter, beginnt nicht mit einer Anrede."""
    parts = (n or "").split()
    return len(parts) >= 2 and parts[0].lower().rstrip(".") not in (
        a.rstrip(".") for a in _ANREDE) and not _generic_name(n)


def _form_name(subject: str) -> str:
    """Name aus dem Formular-Betreff: „Reiseanfrage ⚓ Ziel — 2 Erw. — Erika Muster"."""
    parts = [x.strip() for x in re.split(r"[—–]", subject or "") if x.strip()]
    return parts[-1] if len(parts) >= 2 and _full_name(parts[-1]) else ""


def _name_parts(n: str) -> tuple[str, str]:
    """(Vorname, Nachname) normalisiert, Anrede entfernt; Umlaute aufgelöst.

    Vorname nur bei ≥2 Namensteilen: 'Herr Kühne' → ('', 'kuehne').
    Firmen-/Sammelabsender ('Booking | Executive Cruises GER') liefern nichts.
    """
    if not n or "@" in n or "|" in n:
        return "", ""
    parts = [w for w in n.split() if w.strip(".").lower() not in
             tuple(a.rstrip(".") for a in _ANREDE)]
    if not parts or len(parts) > 3:
        return "", ""

    def _norm(s: str) -> str:
        s = s.lower()
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            s = s.replace(a, b)
        return re.sub(r"[^a-z0-9]", "", s)

    return (_norm(parts[0]) if len(parts) >= 2 else ""), _norm(parts[-1])


def _summarize(subject: str, who: str, preview: str, outgoing: bool = False) -> dict:
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if outgoing:
        prompt = (
            "Hier ist eine GESENDETE Antwort eines Kreuzfahrt-Reisebüros an einen Kunden. "
            "Fasse in MAXIMAL 2 kurzen deutschen Sätzen zusammen, was dem Kunden mitgeteilt "
            "oder zugesagt wurde. Antworte NUR als JSON, ohne weiteren Text:\n"
            '{"zusammenfassung":"<max. 2 Sätze>",'
            '"name":"<VOR- und Nachname des Kunden, z.B. \\"Erika Musterfrau\\" – suche im '
            'GESAMTEN Text (zitierte Kunden-Mail, Signatur, Anrede). Nur wenn nirgends ein '
            'Vorname steht: Anrede + Nachname (z.B. \\"Frau Baumann\\"); leer wenn unklar>"}\n'
            "Der Text kann technisch abgeschnitten sein – erwähne das NICHT.\n\n"
            f"Empfänger: {who}\nBetreff: {subject}\nText: {preview[:1800]}"
        )
    else:
        prompt = (
            "Hier ist die jüngste E-Mail aus dem Buchungspostfach eines Kreuzfahrt-Reisebüros. "
            "Fasse den Vorgang in MAXIMAL 3 kurzen deutschen Sätzen zusammen – Fokus auf Stand "
            "und vor allem Probleme oder offene Aufgaben. Antworte NUR als JSON, ohne weiteren Text:\n"
            '{"zusammenfassung":"<max. 3 Sätze>","problem":true|false,'
            '"name":"<VOR- und Nachname des Kunden, z.B. \\"Erika Musterfrau\\" – suche im '
            'GESAMTEN Text (Signatur, Formularfelder, Anrede, zitierte Mail). Nur wenn nirgends '
            'ein Vorname steht: Anrede + Nachname (z.B. \\"Frau Baumann\\"); leer wenn unklar>",'
            '"anfrage":true|false}\n'
            "problem=true nur bei echtem Handlungsbedarf (Beschwerde, Zahlungsproblem, Stornowunsch, "
            "dringende/offene Frage, Fehler). Sonst false.\n"
            "anfrage=true, wenn ein Kunde eine NEUE Reise-/Buchungs-/Beratungsanfrage stellt "
            "(Lead: möchte Angebot, Verfügbarkeit, Kabine, Preis – noch keine bestehende Buchung). "
            "Reine Rückfragen zu bestehenden Buchungen, Reederei-Systemmails, Newsletter: false.\n"
            "Der Text kann technisch abgeschnitten sein – erwähne das NICHT, fasse nur den "
            "erkennbaren Inhalt zusammen.\n\n"
            f"Absender: {who}\nBetreff: {subject}\nText: {preview[:1800]}"
        )
    r = client.messages.create(model=MODEL, max_tokens=250, temperature=0,
                               messages=[{"role": "user", "content": prompt}])
    text, problem = preview[:160], False
    name, anfrage = "", False
    m = re.search(r"\{.*\}", r.content[0].text, re.S)
    if m:
        try:
            d = json.loads(m.group())
            text = str(d.get("zusammenfassung", text)).strip()
            problem = bool(d.get("problem", False)) and not outgoing
            name = str(d.get("name", "")).strip()
            anfrage = bool(d.get("anfrage", False)) and not outgoing
        except (ValueError, TypeError):
            pass
    return {"zusammenfassung": text, "problem": problem, "name": name, "anfrage": anfrage}


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
        subject = m.get("subject", "") or ""
        who = _recipient(m)[0] if direction == "out" else _sender(m)
        addr = (_recipient(m)[1] if direction == "out" else _addr(m)).lower()

        # Website-Reiseanfrage: KEINE KI. Das Formular ist strukturiert – Name, echte
        # Adresse und Wünsche stehen als Felder drin, genau wie in der CRM-Liste. Die
        # KI hat daraus mal eine Zusammenfassung gemacht und mal (bei einem API-Fehler)
        # einen Roh-Textblock; die Relay-Adresse formresponses@… als Identität hat
        # zusätzlich alle Formular-Anfragen eines Tages zu EINER Person verschmolzen.
        if direction == "in" and webform.is_form(addr, subject):
            roh = (m.get("body") or {}).get("content", "")
            body = webform.plain(roh)
            form = webform.parse(roh)
            rt = (m.get("replyTo") or [{}])[0].get("emailAddress") or {}
            # Relay-Adresse nie übernehmen: lieber gar keine, sonst erbt der nächste
            # Formular-Kunde den Namen des vorigen (Adress-Verzeichnis weiter unten).
            addr = ((rt.get("address") or "").lower()
                    or webform.clean_mail(form.get("mail")) or "")
            name = (form.get("kunde") or _form_name(subject) or rt.get("name") or "").strip()
            info = {"zusammenfassung": webform.wish_line(form) or body[:200],
                    "problem": False, "name": name, "anfrage": True}
        else:
            # ":v3"/":out3": erzwingt EINMALIGE Neu-Zusammenfassung bei Prompt-Änderung
            # (aktuell: Namens-Extraktion MIT Vornamen aus dem gesamten Text).
            key = f"{cid}:{m['receivedDateTime']}" + (":out3" if direction == "out" else ":v3")
            if key in cache:
                info = cache[key]
            else:
                try:
                    info = _summarize(subject, who, _body_text(m),
                                      outgoing=(direction == "out"))
                    cache[key] = info          # NUR Erfolge cachen – ein einmaliger
                    changed = True             # API-Fehler blieb sonst für immer stehen
                except Exception:  # noqa: BLE001
                    info = {"zusammenfassung": (m.get("bodyPreview", "") or "")[:150],
                            "problem": False}
            name = str(info.get("name", "") or "").strip()

        stamp = (m.get("sentDateTime") if direction == "out" else m["receivedDateTime"]) or m["receivedDateTime"]
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
        # Anzeige: echter Kundenname schlägt generische Absender/Empfänger
        # ('morr.de' beim Website-Formular, nackte Adressen)
        kontakt = name if (name and _generic_name(who)) else (who or name)
        out.append({"kontakt": kontakt, "name": name, "addr": addr,
                    "betreff": subject,
                    "text": info.get("zusammenfassung", ""),
                    "problem": bool(info.get("problem")) and direction == "in",
                    "anfrage": bool(info.get("anfrage")) and direction == "in",
                    "direction": direction, "date": when.date().isoformat(),
                    "time": when.strftime("%H:%M"),
                    "cid": cid})   # Konversation: bündelt eingehend + gesendete Antwort

    # Namens-Verzeichnis Adresse -> bester voller Name (MIT Vorname): kennt IRGENDEINE
    # Mail der Person den vollen Namen (Signatur, Absendername), erben ihn alle ihre
    # Einträge – statt „Frau Baumann" hier und Adresse dort.
    best: dict[str, str] = {}
    for it in out:
        if it["addr"] and not best.get(it["addr"]):
            for cand in (it.get("name", ""), it.get("kontakt", "")):
                if _full_name(cand):
                    best[it["addr"]] = cand
                    break
    for it in out:
        b = best.get(it["addr"])
        if b:
            if not _full_name(it.get("name", "")):
                it["name"] = b
            if not _full_name(it.get("kontakt", "")):
                it["kontakt"] = b

    # Zweites Verzeichnis über den NACHNAMEN. Nötig, weil Website-Anfragen von
    # formresponses@… kommen, die Antwort darauf aber an die echte Kundenadresse geht –
    # über die Adresse finden „Werner Kühne" (Formular) und „Herr Kühne" (Antwort) nie
    # zusammen. Nur eindeutige Nachnamen: kommen zum selben Nachnamen zwei verschiedene
    # Vornamen vor, sind es zwei Kunden und es wird nichts vererbt.
    voll: dict[str, set[str]] = {}
    for it in out:
        for cand in (it.get("name", ""), it.get("kontakt", "")):
            if _full_name(cand):
                voll.setdefault(_name_parts(cand)[1], set()).add(cand)
    best_nach = {nach: max(namen, key=len) for nach, namen in voll.items()
                 if len({_name_parts(n)[0] for n in namen}) == 1}
    for it in out:
        b = best_nach.get(_name_parts(it.get("kontakt") or it.get("name") or "")[1])
        if b:
            if not _full_name(it.get("name", "")):
                it["name"] = b
            if not _full_name(it.get("kontakt", "")):
                it["kontakt"] = b

    if changed:
        _save_cache(cache)
    # je Tag: Probleme oben, dann eingehend vor gesendet
    out.sort(key=lambda x: (x["date"], x["problem"], x["direction"] == "in"), reverse=True)
    return out
