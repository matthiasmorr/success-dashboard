"""🤝 Sales-Leads: die Menschen hinter dem buchung@-Postfach als CRM-Liste.

Jede Person, mit der in den letzten 30 Tagen gemailt wurde, wird EIN Lead-Eintrag:
Kontaktverlauf (wann zuletzt, wie viele Mails, beantwortet?), die Wünsche aus dem
Website-Formular (Ziel, Zeitraum, Budget, Kabine, Personen), der Newsletter-Status
aus Kit (Status + Tags) und – falls vorhanden – der passende Vorgang aus dem Ledger
(Option/Festbuchung). Damit ist auf einen Blick sichtbar: wer wartet auf Antwort,
wer ist heiß, wer ist noch nicht im Morrletter.

Identität = E-Mail-Adresse. Website-Anfragen kommen technisch von
formresponses@netlify.com – die echte Kundenadresse steht im Reply-To und wird
darüber aufgelöst, sonst wäre jede Formular-Anfrage derselbe „Kontakt".

Kit-Abfragen (2 Requests je Adresse) werden dauerhaft in data/kit_lookup_cache.json
gecacht; ein Lauf mit warmem Cache kostet keine API-Aufrufe.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests

from . import graph, ledger, webform
from .base import Category, ConnectorResult, Metric
from .postfach_summary import _form_name, _full_name, _generic_name, _name_parts

NAME = "Sales-Leads"
CAT = Category.LEADS

DAYS = int(os.getenv("LEADS_DAYS", "30"))
FOLDERS = ("Posteingang", "Reisebuchungen", "Anfragen")
SENT_FOLDER = "Gesendete Elemente"

# Automaten und interne/geschäftliche Gegenstellen sind keine Leads
SKIP_ADDR = re.compile(r"no-?reply|no_reply|donotreply|mailer-daemon|postmaster|bounce", re.I)
SKIP_DOMAINS = {d.strip().lower() for d in os.getenv(
    "LEADS_SKIP_DOMAINS",
    "morr.de,kreuzfahrtstudio.de,executivecruises.eu,xmlteam.de,netlify.com,"
    "tuicruises.com,aida.de,msc-kreuzfahrten.de,costa.it,hurtigruten.de",
).split(",") if d.strip()}

KIT_API = "https://api.kit.com/v4"
KIT_CACHE = os.getenv("KIT_LOOKUP_CACHE", "data/kit_lookup_cache.json")
KIT_TTL_DAYS = 5            # Status/Tags ändern sich selten – Cache reicht lange
KIT_MAX_LOOKUPS = int(os.getenv("LEADS_KIT_MAX_LOOKUPS", "150"))   # Rate-Limit-Schutz

KIT_STATE_LABEL = {"active": "aktiv", "inactive": "inaktiv", "cancelled": "abgemeldet",
                   "unsubscribed": "abgemeldet", "bounced": "unzustellbar",
                   "complained": "Beschwerde"}

_SELECT_IN = ("subject,from,replyTo,receivedDateTime,body,conversationId")
_SELECT_OUT = ("subject,toRecipients,receivedDateTime,sentDateTime,conversationId")


# ===================== Website-Formular =====================
# Feld-Parser + Wunsch-Zeile liegen in `webform` (teilt sich die Postfach-Aktivität).


def _budget_eur(form: dict) -> float:
    """Budget als Zahl – „6000", „ca. 8.000 €", „5000-7000" (dann die Obergrenze)."""
    raw = (form.get("budget") or "").replace(".", "").replace(",", ".")
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", raw)]
    nums = [n for n in nums if n >= 100]     # „2 Pers." o.ä. ist kein Budget
    return max(nums) if nums else 0.0

# ===================== Kit (ConvertKit) =====================
def _kit_cache() -> dict:
    try:
        with open(KIT_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _kit_save(cache: dict) -> None:
    os.makedirs(os.path.dirname(KIT_CACHE) or ".", exist_ok=True)
    with open(KIT_CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)


def _kit_fetch(key: str, email: str) -> dict:
    """{state, created_at, first_name, tags[]} – leeres state = nicht im Morrletter."""
    h = {"X-Kit-Api-Key": key, "Accept": "application/json"}
    r = requests.get(f"{KIT_API}/subscribers", params={"email_address": email},
                     headers=h, timeout=20)
    r.raise_for_status()
    subs = r.json().get("subscribers") or []
    if not subs:
        return {"state": "", "created_at": "", "first_name": "", "tags": []}
    s = subs[0]
    tags: list[str] = []
    t = requests.get(f"{KIT_API}/subscribers/{s['id']}/tags", headers=h, timeout=20)
    if t.ok:
        tags = [x.get("name", "") for x in (t.json().get("tags") or []) if x.get("name")]
    return {"state": s.get("state", ""), "created_at": s.get("created_at", ""),
            "first_name": s.get("first_name") or "", "tags": tags}


def _kit_enrich(emails: list[str]) -> dict[str, dict]:
    """Kit-Status + Tags je Adresse. Cache-first; ohne Key/bei Rate-Limit einfach leer."""
    cache = _kit_cache()
    key = os.getenv("KIT_API_KEY", "").strip()
    fresh = (datetime.now(timezone.utc) - timedelta(days=KIT_TTL_DAYS)).isoformat()
    out, changed, budget = {}, False, KIT_MAX_LOOKUPS
    for mail in emails:
        hit = cache.get(mail)
        if hit and hit.get("ts", "") >= fresh:
            out[mail] = hit
            continue
        if not key or budget <= 0:
            if hit:
                out[mail] = hit          # abgelaufen, aber besser als nichts
            continue
        budget -= 1
        try:
            info = _kit_fetch(key, mail)
        except Exception:  # noqa: BLE001 – Rate-Limit/Aussetzer: alten Stand behalten
            budget = 0
            if hit:
                out[mail] = hit
            continue
        info["ts"] = datetime.now(timezone.utc).isoformat()
        cache[mail] = info
        out[mail] = info
        changed = True
    if changed:
        _kit_save(cache)
    return out


# ===================== Kontakte aus dem Postfach =====================
def _skip(addr: str) -> bool:
    a = (addr or "").lower()
    return (not a or "@" not in a or bool(SKIP_ADDR.search(a))
            or a.split("@")[-1] in SKIP_DOMAINS)


def _touch(book: dict, addr: str, name: str, when: datetime, direction: str,
           subject: str) -> dict:
    """Kontakt anlegen/fortschreiben und den Eintrag zurückgeben."""
    c = book.get(addr)
    if c is None:
        c = book[addr] = {"email": addr, "name": "", "names": [], "n_in": 0, "n_out": 0,
                          "first": None, "last": None, "last_dir": "", "betreff": "",
                          "form": {}, "form_date": "", "anfrage": False}
    if name and not _generic_name(name):
        c["names"].append(name)
    c["n_in" if direction == "in" else "n_out"] += 1
    if c["first"] is None or when < c["first"]:
        c["first"] = when
    if c["last"] is None or when >= c["last"]:
        c["last"], c["last_dir"] = when, direction
        if direction == "in" and subject:
            c["betreff"] = subject
    return c


def _collect(days: int) -> dict[str, dict]:
    """Alle Gesprächspartner der letzten `days` Tage, je Adresse ein Eintrag."""
    since = date.today() - timedelta(days=days)
    book: dict[str, dict] = {}

    for folder in FOLDERS:
        for m in graph.messages_since(folder, since, _SELECT_IN):
            ea = ((m.get("from") or {}).get("emailAddress") or {})
            addr, disp = (ea.get("address") or "").lower(), ea.get("name") or ""
            subject = m.get("subject", "") or ""
            form: dict = {}
            if webform.is_form(addr, subject):
                # Website-Formular: echte Kundenadresse steht im Reply-To
                rt = (m.get("replyTo") or [{}])[0].get("emailAddress") or {}
                form = webform.parse((m.get("body") or {}).get("content", ""))
                addr = ((rt.get("address") or "").lower() or webform.clean_mail(form.get("mail"))
                        or addr)
                disp = form.get("kunde") or _form_name(subject) or rt.get("name") or disp
            if _skip(addr):
                continue
            when = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).astimezone()
            c = _touch(book, addr, disp, when, "in", subject)
            if form and when.date().isoformat() >= (c.get("form_date") or ""):
                c["form"], c["form_date"], c["anfrage"] = form, when.date().isoformat(), True

    for m in graph.messages_since(SENT_FOLDER, since, _SELECT_OUT):
        for rcp in (m.get("toRecipients") or [])[:1]:
            ea = rcp.get("emailAddress") or {}
            addr = (ea.get("address") or "").lower()
            if _skip(addr):
                continue
            stamp = m.get("sentDateTime") or m["receivedDateTime"]
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
            _touch(book, addr, ea.get("name") or "", when, "out", m.get("subject", "") or "")
    return book


def _best_name(names: list[str], email: str, kit_first: str = "") -> str:
    """Bester Anzeigename: voller Name > Anrede+Nachname > Kit-Vorname > Adresse."""
    voll = [n for n in names if _full_name(n)]
    if voll:
        return max(voll, key=len)
    rest = [n for n in names if n and "@" not in n]
    if rest:
        return max(rest, key=len)
    return kit_first or email


# ===================== Vorgangs-Abgleich (Ledger) =====================
def _vorgang_index() -> dict[str, dict]:
    """Nachname → bester Vorgang (Festbuchung schlägt Option)."""
    try:
        led = ledger._load()
    except Exception:  # noqa: BLE001
        return {}
    rank = {"option": 1, "festbuchung": 2}
    idx: dict[str, dict] = {}
    for e in led.values():
        nach = _name_parts(e.get("nachname") or "")[1]
        if not nach or e.get("storno_date"):
            continue
        cur = idx.get(nach)
        if cur is None or rank.get(e.get("state"), 0) > rank.get(cur.get("state"), 0):
            idx[nach] = e
    return idx


# ===================== öffentliche API =====================
def summary(days: int = DAYS) -> dict | None:
    """Lead-Liste + Kennzahlen. None, wenn das Postfach nicht angebunden ist."""
    if not graph.configured():
        return None
    book = _collect(days)
    kit = _kit_enrich(sorted(book))
    vidx = _vorgang_index()
    today = date.today()

    leads = []
    for addr, c in book.items():
        k = kit.get(addr) or {}
        name = _best_name(c["names"], addr, k.get("first_name", ""))
        vg = vidx.get(_name_parts(name)[1]) if _name_parts(name)[1] else None
        last: datetime = c["last"]
        offen = c["last_dir"] == "in"          # letzte Mail kam vom Kunden = wartet
        leads.append({
            "name": name, "email": addr,
            "n_in": c["n_in"], "n_out": c["n_out"],
            "erstkontakt": c["first"].date().isoformat(),
            "datum": last.date().isoformat(), "zeit": last.strftime("%H:%M"),
            "tage_still": (today - last.date()).days,
            "offen": offen, "betreff": c["betreff"],
            "anfrage": c["anfrage"], "form": c["form"], "budget": _budget_eur(c["form"]),
            "telefon": (c["form"].get("telefon") or "")[:24],
            "kit_state": k.get("state", ""), "kit_since": (k.get("created_at") or "")[:10],
            "kit_label": KIT_STATE_LABEL.get(k.get("state", ""), k.get("state", "")),
            "kit_tags": k.get("tags", []),
            "vorgang": (vg or {}).get("state", ""), "vorgang_wert": (vg or {}).get("value", 0),
            "vorgang_label": (vg or {}).get("label", ""),
        })

    # Reihenfolge = Handlungsdruck: wartende Anfragen zuerst, dann wartende Kontakte,
    # dann nach letzter Aktivität. Genau die Sortierung, in der man sie abarbeitet.
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
    }


def fetch() -> ConnectorResult:
    if not graph.configured():
        return ConnectorResult.missing_config(NAME, CAT, "MS-Graph-Zugang fehlt (.env)")
    try:
        s = summary()
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e)[:200])
    if s is None:
        return ConnectorResult.missing_config(NAME, CAT, "Postfach nicht verbunden")

    d = s["days"]
    quote = (s["n_kit"] / s["n_kontakte"] * 100) if s["n_kontakte"] else 0.0
    metrics = [
        Metric("Kontakte", s["n_kontakte"], delta=f"{d} Tage", delta_color="off",
               help=f"Personen, mit denen im buchung@-Postfach in {d} Tagen gemailt wurde "
                    "(Automaten, interne und Reederei-Adressen herausgerechnet)."),
        Metric("Warten auf Antwort", s["n_offen"], delta=f"{s['n_anfragen']} Anfragen",
               delta_color="off",
               help="Letzte Mail kam vom Kunden – noch keine Antwort aus dem Postfach. "
                    "Delta = neue Reiseanfragen über das Website-Formular."),
        Metric("Im Morrletter", f"{quote:.0f} %".replace(".", ","),
               delta=f"{s['n_kit']} von {s['n_kontakte']}", delta_color="off",
               help=f"Anteil der Kontakte, die in Kit als Abonnent geführt werden "
                    f"(davon {s['n_kit_aktiv']} aktiv). Der Rest ist ungenutztes Potenzial."),
        Metric("Anfrage → Vorgang", f"{s['conversion']:.0f} %".replace(".", ","),
               delta=f"{s['n_vorgang']} Vorgänge · {s['n_gebucht']} fest", delta_color="off",
               help="Anteil der Website-Anfragen, zu denen es im Vorgangs-Ledger bereits "
                    "eine Option oder Festbuchung gibt (Abgleich über den Nachnamen)."),
        Metric("Wunschbudget", f"{s['budget_summe']:,.0f} €".replace(",", "."),
               delta=f"{s['n_anfragen']} Anfragen", delta_color="off",
               help="Summe der im Website-Formular genannten Gesamtbudgets der letzten "
                    f"{d} Tage. Grobe Potenzialschätzung, keine Pipeline."),
    ]
    return ConnectorResult(
        name=NAME, category=CAT, metrics=metrics,
        caption=f"{s['n_kontakte']} Kontakte · {s['n_nokit']} noch nicht im Morrletter",
        hero_sections=[{"title": "🤝 Sales-Leads", "metrics": metrics, "list": None,
                        "leads": s["leads"], "stats": s}])
