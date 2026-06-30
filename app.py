"""Erfolgs-Dashboard – strategischer Wachstums-Überblick für morr.de.

Phase 1 (MVP, lokal): Einnahmen (Digistore, Awin) + Reichweite (YouTube, KIT) per API.
Start:  ./venv/bin/streamlit run app.py
"""
from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from connectors import ALL_CONNECTORS, Category, snapshot
from connectors.digistore import _euro

# Zeitzone fest auf Europe/Berlin – der Cloud-Server läuft sonst in UTC (2h Versatz
# bei „Stand" UND bei der „heute"/Tagesgrenzen-Logik).
os.environ["TZ"] = "Europe/Berlin"
if hasattr(time, "tzset"):
    time.tzset()

load_dotenv()

# Streamlit Community Cloud: Secrets → os.environ, damit die Connectoren (os.getenv)
# sie wie lokal aus der .env lesen. Lokal hat .env Vorrang (setdefault überschreibt nicht).
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:  # noqa: BLE001 – keine secrets.toml vorhanden (lokal ohne Secrets)
    pass

st.set_page_config(page_title="Daily Morr", page_icon="static/app-icon.png", layout="wide")

# Home-Screen-Icon (iOS „Zum Home-Bildschirm") + App-Name. Streamlit serviert
# static/ unter app/static/ (enableStaticServing in config.toml). iOS liest
# apple-touch-icon nur aus dem <head> – st.markdown landet im Body, daher per JS
# in window.parent.document.head injizieren (zuverlässig auch beim Hinzufügen).
st.components.v1.html(
    """
    <script>
    const d = window.parent.document;
    const set = (rel, href) => {
      let l = d.querySelector('link[rel="' + rel + '"]');
      if (!l) { l = d.createElement('link'); l.setAttribute('rel', rel); d.head.appendChild(l); }
      l.setAttribute('href', href);
    };
    set('apple-touch-icon', 'app/static/app-icon.png');
    set('apple-touch-icon-precomposed', 'app/static/app-icon.png');
    const meta = (name, content) => {
      let m = d.querySelector('meta[name="' + name + '"]');
      if (!m) { m = d.createElement('meta'); m.setAttribute('name', name); d.head.appendChild(m); }
      m.setAttribute('content', content);
    };
    meta('apple-mobile-web-app-title', 'Daily Morr');
    meta('apple-mobile-web-app-capable', 'yes');
    </script>
    """,
    height=0,
)

# --- morr.de-Branding (Farben/Schriften aus der Astro-Site) ---
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..700&family=Lato:wght@400;700&display=swap');
    html, body, [class*="st-"], .stMarkdown { font-family: 'Lato', sans-serif; }
    h1, h2, h3, h4 { font-family: 'Fraunces', serif !important; color: #1B1B6D; letter-spacing: -0.01em; }
    /* Gebrandeter Kopf mit M-Logo */
    .app-header { display:flex; align-items:center; gap:12px; margin:0 0 0; }
    .app-logo svg { width:52px; height:52px; display:block; }
    .app-title { font-family:'Fraunces',serif; font-weight:700; font-size:2.1rem;
        color:#1B1B6D; line-height:1; letter-spacing:-0.01em; white-space:nowrap; }
    /* Kacheln im Magnolia-Karten-Look – alle gleich hoch */
    [data-testid="stMetric"] {
        background: #F1F0FA;
        border: 1px solid #e7e6f7;
        border-radius: 14px;
        padding: 14px 18px;
        min-height: 132px;
    }
    [data-testid="stMetricValue"] { color: #1B1B6D; font-weight: 700;
        font-size: 1.75rem; line-height: 1.15; overflow: visible; white-space: normal; }
    [data-testid="stMetricValue"] > * { overflow: visible !important;
        text-overflow: clip !important; white-space: normal !important; }
    [data-testid="stMetricLabel"] p { color: #5a5a86; font-weight: 700; }
    /* Delta = nur grauer Kontexttext (Gestern/Monat/…); Streamlits Auto-Pfeil raus,
       Text darf umbrechen statt abgeschnitten zu werden. */
    [data-testid="stMetricDelta"] svg { display: none; }
    [data-testid="stMetricDelta"] { white-space: normal; line-height: 1.25;
        gap: 0 !important; margin-top: 2px; }
    [data-testid="stMetricDelta"] > div { white-space: normal; }
    /* Section-Header dezente Linie in Persian */
    h2 { border-bottom: 2px solid #e7e6f7; padding-bottom: .25rem; }
    /* Tab-Navigation: gut sicht- und tappbar, Marke; klebt oben beim Scrollen */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: 4px; position: sticky; top: 0; z-index: 50;
        background: #ffffff; padding: 6px 0 2px; border-bottom: 1px solid #e7e6f7; }
    [data-testid="stTabs"] [data-baseweb="tab"] {
        flex: 1; justify-content: center; border-radius: 10px 10px 0 0;
        padding: 8px 6px !important; font-weight: 700; color: #5a5a86; }
    [data-testid="stTabs"] [aria-selected="true"] { color: #1B1B6D; background: #F1F0FA; }
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: #3636D9; height: 3px; }
    [data-testid="stTabs"] [data-baseweb="tab-border"] { display: none; }
    /* Tages-Pills (Buttons als Datums-Navigator) */
    .stButton > button { border-radius:999px !important; padding:4px 12px !important;
        font-weight:700 !important; font-size:.9rem !important; }
    .stButton > button[kind="secondary"] { background:#F1F0FA !important; color:#5a5a86 !important;
        border:1px solid #e7e6f7 !important; }
    .stButton > button[kind="primary"] { box-shadow:0 3px 10px rgba(54,54,217,.25) !important; }
    /* Zentrum: Erfolg-heute-Band */
    .erfolg-band {
        background: linear-gradient(135deg, #1B1B6D 0%, #3636D9 100%);
        border-radius: 18px; padding: 18px 26px; margin: 10px 0 12px;
        box-shadow: 0 6px 24px rgba(27,27,109,.18);
    }
    .erfolg-label { font-family:'Fraunces',serif; color:#F1F0FA; opacity:.85;
        font-size:1.05rem; letter-spacing:.01em; }
    .erfolg-value { font-family:'Fraunces',serif; color:#ffffff !important; font-weight:700;
        font-size:2.9rem; line-height:1.05; margin:2px 0; }
    .erfolg-sub { color:#F1F0FA; opacity:.8; font-size:.95rem; }
    /* Drei Vergleichs-Chips (gestern · 7 T · 30 T) – kompakt in einer Reihe, damit
       alle vier Erfolgs-Zahlen auf einen Blick passen (auch am Handy). */
    .cmp-row { display:flex; gap:10px; margin:0 0 20px; }
    .cmp-chip { flex:1; min-width:0; background: linear-gradient(135deg, #4d4d9e 0%, #7d7dc0 100%);
        border-radius:14px; padding:11px 13px; box-shadow:0 4px 16px rgba(54,54,158,.12); }
    .cmp-label { font-family:'Fraunces',serif; color:#F1F0FA; opacity:.85;
        font-size:.82rem; letter-spacing:.01em; }
    .cmp-value { font-family:'Fraunces',serif; color:#fff; font-weight:700;
        font-size:1.25rem; line-height:1.1; margin:3px 0 1px; white-space:nowrap; }
    .cmp-sub { color:#F1F0FA; opacity:.75; font-size:.72rem; }
    /* Reichweite-Mini-Leiste direkt unter den Erfolgs-Kacheln (bewusst klein) */
    .grow-row { display:flex; gap:7px; margin:2px 0 18px; flex-wrap:wrap; }
    .grow-item { flex:1; min-width:78px; background:#F1F0FA; border:1px solid #e7e6f7;
        border-radius:10px; padding:6px 9px; }
    .grow-label { color:#5a5a86; font-size:.68rem; font-weight:700; line-height:1.2;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    /* Nur die Änderung seit gestern – bewusst groß (Tageswachstum auf einen Blick) */
    .grow-change { font-family:'Fraunces',serif; font-weight:700; font-size:1.6rem;
        line-height:1.2; margin-top:1px; }
    .grow-change.pos { color:#2f7a2f; }
    .grow-change.neg { color:#b23b3b; }
    .grow-change.zero { color:#8a8ab5; }
    /* Auflistung Buchungen/Optionen */
    .bk-list { margin: 4px 0 12px; border:1px solid #e7e6f7; border-radius:12px; overflow:hidden; }
    .bk-row { display:flex; align-items:center; gap:12px; padding:8px 14px; border-bottom:1px solid #efeefa; }
    .bk-row:last-child { border-bottom:none; }
    .bk-badge { font-size:.7rem; font-weight:700; padding:3px 10px; border-radius:999px;
        color:#fff; min-width:70px; text-align:center; letter-spacing:.02em; }
    .b-buchung { background:#1B1B6D; }              /* Indigo = feste Buchung (realisiert) */
    .b-option  { background:#D6D4F2; color:#1B1B6D; }  /* helles Lavendel = Option (vorläufig) */
    .bk-val { font-weight:700; color:#1B1B6D; min-width:104px; }
    .bk-name { font-weight:700; color:#1B1B6D; min-width:120px; }
    .bk-label { color:#3a3a5a; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    /* Postfach-Aktivität (KI-Zusammenfassung) */
    .act-list { margin:4px 0 14px; display:flex; flex-direction:column; gap:8px; }
    .act-row { border:1px solid #e7e6f7; border-left:4px solid #3636D9; border-radius:10px;
        padding:10px 14px; background:#fbfbfe; }
    .act-problem { border-left-color:#d98a1f; background:#fff8ef; }
    .act-out { border-left-color:#3a8a3a; background:#f5fbf5; }   /* gesendete Antwort */
    .act-flag { font-size:.66rem; font-weight:700; color:#2f7a2f; background:#e4f3e4;
        border-radius:999px; padding:1px 8px; }
    .act-head { display:flex; align-items:center; gap:8px; }
    .act-kontakt { font-weight:700; color:#1B1B6D; }
    .act-date { color:#8a8ab5; font-size:.82rem; }
    .act-betreff { color:#5a5a86; font-size:.84rem; margin:1px 0 3px; }
    .act-text { color:#3a3a5a; font-size:.92rem; line-height:1.35; }
    .act-reply { color:#2f7a2f; font-weight:700; }
    .bk-date { color:#8a8ab5; font-size:.85rem; }
    /* Lauf-Indikator oben rechts: Streamlits rennendes Männchen raus, maritimes Schiff rein */
    [data-testid="stStatusWidgetRunningManIcon"] { display:none !important; }
    [data-testid="stStatusWidgetRunningIcon"] { display:inline-flex; align-items:center;
        justify-content:center; width:1.6rem; height:1.6rem; }
    [data-testid="stStatusWidgetRunningIcon"]::before {
        content:"🚢"; font-size:1.3rem; line-height:1; display:inline-block;
        animation:morr-sail 1.4s ease-in-out infinite; transform-origin:50% 80%; }
    @keyframes morr-sail {
        0%,100% { transform:translateY(0) rotate(-7deg); }
        50%     { transform:translateY(-3px) rotate(7deg); } }
    </style>
    """,
    unsafe_allow_html=True,
)

_logo_file = Path(__file__).with_name("assets") / "m-blue.svg"
_logo_svg = ""
if _logo_file.exists():
    _t = _logo_file.read_text(encoding="utf-8")
    _logo_svg = _t[_t.find("<svg"):]   # XML-Deklaration weglassen
st.markdown(
    f'<div class="app-header"><span class="app-logo">{_logo_svg}</span>'
    f'<span class="app-title">Daily Morr</span></div>',
    unsafe_allow_html=True,
)


def _compute_all():
    """Live-Berechnung aller Connectoren (langsam) – inkl. Excel-Refresh aus Drive."""
    from connectors import drive
    try:
        drive.refresh_festbuchungen(max_age_hours=12)
    except Exception:  # noqa: BLE001
        pass
    return [fetch() for fetch in ALL_CONNECTORS]


@st.cache_data(ttl=600, show_spinner="🚢 Daily Morr lädt …")
def load_all(mode: str = "auto"):
    """Connector-Ergebnisse laden. Liefert (results, ts).

    mode:
      "auto"  – lokaler Snapshot (≤45 Min), sonst aus Drive ziehen, sonst live (Standard).
      "drive" – „Aktualisieren": neuesten Hintergrund-Snapshot aus Drive holen (schnell).
                Der wird alle 30 Min von der GitHub-Action / launchd frisch gerechnet.
      "live"  – alle Quellen direkt neu rechnen (langsam, in der Cloud einige Minuten).
    """
    from connectors import drive

    if mode == "live":
        results = _compute_all()
        snapshot.save(results)
        return results, time.time()

    if mode == "drive":
        try:
            drive.download_snapshot()
        except Exception:  # noqa: BLE001
            pass
        snap, ts = snapshot.load()  # was da ist nehmen (Hintergrundjob hält ihn frisch)
        if snap is not None:
            return snap, ts
        # Drive leer/nicht erreichbar → als Fallback live rechnen
        results = _compute_all()
        snapshot.save(results)
        return results, time.time()

    # auto
    snap, ts = snapshot.load(max_age_min=45)
    if snap is not None:
        return snap, ts
    # Kein frischer lokaler Snapshot → in der Cloud den von der GitHub-Action
    # hochgeladenen aus Drive holen, statt 3-5 Min live zu rechnen.
    try:
        drive.download_snapshot()
    except Exception:  # noqa: BLE001
        pass
    snap, ts = snapshot.load(max_age_min=180)
    if snap is not None:
        return snap, ts
    results = _compute_all()
    snapshot.save(results)
    return results, time.time()


col_a, col_b = st.columns([1, 4])
with col_a:
    if st.button("🔄 Aktualisieren",
                 help="Holt den neuesten Hintergrund-Stand (wird alle 30 Min frisch "
                      "gerechnet) – ein paar Sekunden."):
        load_all.clear()
        st.session_state["_mode"] = "drive"
        st.rerun()
with col_b:
    if st.button("🐢 Komplett neu rechnen",
                 help="Rechnet alle Quellen direkt neu. In der Cloud einige Minuten – "
                      "nur nötig, wenn etwas ganz Frisches sofort erscheinen soll."):
        load_all.clear()
        st.session_state["_mode"] = "live"
        st.rerun()

results, _data_ts = load_all(st.session_state.pop("_mode", "auto"))
_stand = datetime.fromtimestamp(_data_ts).strftime("%d.%m.%Y %H:%M") if _data_ts else "—"
st.caption(f"morr.de · Stand {_stand}")

CATEGORY_ICON = {
    Category.HEUTE: "🎯",
    Category.EINNAHMEN: "💶",
    Category.PIPELINE: "📨",
    Category.VANITY: "📣",
}


def _tiles(metrics):
    """Kacheln in Reihen zu max. 4 rendern."""
    per_row = 4 if len(metrics) > 4 else len(metrics)
    for i in range(0, len(metrics), per_row):
        cols = st.columns(per_row)
        for col, m in zip(cols, metrics[i:i + per_row]):
            col.metric(m.label, m.value, delta=m.delta, help=m.help,
                       delta_color=getattr(m, "delta_color", "normal"))


def render_group(res):
    if res.ok and res.metrics:
        st.subheader(res.name)
        _tiles(res.metrics)
        if res.caption:
            st.caption(res.caption)
    elif not res.configured:
        st.info(f"**{res.name}** – noch nicht eingerichtet: {res.error}", icon="⚙️")
    else:
        st.warning(f"**{res.name}** – Fehler beim Abruf: {res.error}", icon="⚠️")


# ===== ZENTRUM: Erfolg heute (+ Vergleich gestern daneben) =====
def _band(b):
    return (
        f'<div class="erfolg-band {b.get("variant", "")}" title="{html.escape(b.get("help", ""))}">'
        f'<div class="erfolg-label">{b["label"]}</div>'
        f'<div class="erfolg-value">{b["value"]}</div>'
        f'<div class="erfolg-sub">{b.get("sub", "")}</div>'
        f'</div>'
    )


def _change_of(m):
    """Die Änderung seit gestern aus einer Wachstums-Metrik herausziehen – kompakt.

    Social/YouTube tragen sie im delta ('+X seit gestern'), Morrletter direkt im
    Wert ('+12' neue Abos heute). Gibt None, wenn noch kein Vergleich vorliegt.
    """
    d = m.delta
    if isinstance(d, str) and "seit gestern" in d:
        return d.split(" seit gestern")[0].strip()
    v = str(m.value)
    if v[:1] in ("+", "-", "−", "±"):   # Wert ist selbst schon eine Änderung (Morrletter)
        return v
    if isinstance(d, (int, float)):
        return f"{'+' if d >= 0 else ''}{int(d)}"
    return None


def _growth_strip(metrics):
    """Reichweite kompakt: je Account NUR die Änderung seit gestern – groß."""
    cells = []
    for m in metrics:
        change = _change_of(m)
        if change in (None, ""):
            big, sign = "–", "zero"
        else:
            big = change
            digits = change.lstrip("+-−± ")
            zero = digits in ("", "0") or set(digits) <= {"0"}
            sign = "zero" if zero else ("neg" if change[:1] in ("-", "−") else "pos")
        cells.append(
            f'<div class="grow-item" title="{html.escape(m.help or "")}">'
            f'<div class="grow-label">{html.escape(m.label)}</div>'
            f'<div class="grow-change {sign}">{html.escape(big)}</div>'
            f'</div>')
    return '<div class="grow-row">' + "".join(cells) + "</div>"


def _chips(items):
    """gestern · 7 Tage · 30 Tage als kompakte Chips in einer Reihe."""
    cells = []
    for b in items:
        label = b["label"].replace("Erfolg ", "")
        # Cents in den Vergleichs-Chips weglassen (glanceable, passt ohne Umbruch);
        # volle Präzision bleibt im großen „heute"-Band.
        val = b["value"].split(",")[0] + " €" if "," in b["value"] else b["value"]
        cells.append(
            f'<div class="cmp-chip" title="{html.escape(b.get("help", ""))}">'
            f'<div class="cmp-label">{label}</div>'
            f'<div class="cmp-value">{val}</div>'
            f'<div class="cmp-sub">{b.get("sub", "")}</div></div>')
    return '<div class="cmp-row">' + "".join(cells) + "</div>"


def _booking_list(items):
    # Alles zu EINER Person bündeln: mehrere Vorgänge desselben Nachnamens (am selben
    # Tag) werden zu einer Zeile – Werte summiert, Reisen gebündelt. Eine feste Buchung
    # sticht die Option. Einträge ohne Namen bleiben einzeln stehen.
    grouped: list[list[dict]] = []
    index: dict[str, list[dict]] = {}
    for it in items[:60]:
        key = (it.get("nachname") or "").strip().lower()
        if key and key in index:
            index[key].append(it)
        else:
            g = [it]
            grouped.append(g)
            if key:
                index[key] = g

    rows = []
    for g in grouped[:20]:
        opt = all(x.get("art") == "option" for x in g)   # nur Option, wenn KEINE feste Buchung dabei
        badge = "Option" if opt else "Buchung"
        cls = "b-option" if opt else "b-buchung"
        total = sum(x.get("value", 0) or 0 for x in g)
        iso = max((x.get("date", "") for x in g), default="")
        tag = f"{iso[8:10]}.{iso[5:7]}." if len(iso) >= 10 else ""
        name = html.escape(g[0].get("nachname", "") or "")
        name_html = f'<span class="bk-name">{name}</span>' if name else ""
        labels = list(dict.fromkeys(x.get("label", "") for x in g if x.get("label")))
        label_txt = " · ".join(labels)
        if len(g) > 1:   # mehrere Vorgänge einer Person sichtbar machen
            n = f"{len(g)} Vorgänge"
            label_txt = f"{n} · {label_txt}" if label_txt else n
        rows.append(
            f'<div class="bk-row"><span class="bk-badge {cls}">{badge}</span>'
            f'<span class="bk-val">{_euro(total)}</span>'
            f'{name_html}'
            f'<span class="bk-label">{html.escape(label_txt)}</span>'
            f'<span class="bk-date">{tag}</span></div>')
    return '<div class="bk-list">' + "".join(rows) + "</div>"


def _activity_list(items):
    # Alles zu EINER Person bündeln: eingehende Mail + gesendete Antwort eines Vorgangs
    # (und mehrere Vorgänge derselben Person) erscheinen in EINER Karte – nicht mehr als
    # getrennte Felder. Frank Tente = ein Feld mit Anliegen + Antwort.
    def _name_key(it):
        n = (it.get("kontakt") or "").strip()
        return re.sub(r"[^a-z0-9äöüß]", "", n.lower()) if (n and "@" not in n) else ""

    # 1) nach Konversation gruppieren (verbindet eingehend + gesendet zuverlässig)
    groups: list[list[dict]] = []
    by_cid: dict[str, list[dict]] = {}
    for it in items:
        cid = it.get("cid") or ""
        if cid and cid in by_cid:
            by_cid[cid].append(it)
        else:
            g = [it]
            groups.append(g)
            if cid:
                by_cid[cid] = g
    # 2) Gruppen derselben Person (gleicher Anzeigename) zusätzlich zusammenführen
    merged: list[list[dict]] = []
    by_name: dict[str, list[dict]] = {}
    for g in groups:
        nk = next((_name_key(x) for x in g if _name_key(x)), "")
        if nk and nk in by_name:
            by_name[nk].extend(g)
        else:
            merged.append(g)
            if nk:
                by_name[nk] = g

    rows = []
    for g in merged:
        kontakt = next((x.get("kontakt") for x in g
                        if x.get("kontakt") and "@" not in x.get("kontakt")),
                       g[0].get("kontakt", "")) or ""
        ins = [x for x in g if x.get("direction") != "out"]
        outs = [x for x in g if x.get("direction") == "out"]
        answered = bool(outs)
        problem = any(x.get("problem") for x in ins) and not answered
        cls = "act-out" if answered else ("act-problem" if problem else "")
        lead = "✅ " if answered else ("⚠️ " if problem else "")
        flag = '<span class="act-flag">beantwortet</span>' if answered else ""
        iso = max((x.get("date", "") for x in g), default="")
        tag = f"{iso[8:10]}.{iso[5:7]}." if len(iso) >= 10 else ""
        betreff = (ins[0].get("betreff") if ins else g[0].get("betreff", "")) or ""
        lines = [html.escape(x.get("text", "")) for x in ins if x.get("text")]
        lines += ['<span class="act-reply">↗️ Antwort:</span> ' + html.escape(x.get("text", ""))
                  for x in outs if x.get("text")]
        body = "<br>".join(lines)
        rows.append(
            f'<div class="act-row {cls}">'
            f'<div class="act-head">{lead}<span class="act-kontakt">{html.escape(kontakt)}</span>'
            f'{flag}<span class="act-date">{tag}</span></div>'
            f'<div class="act-betreff">{html.escape(betreff)}</div>'
            f'<div class="act-text">{body}</div></div>')
    return '<div class="act-list">' + "".join(rows) + "</div>"


_WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _render_section(sec):
    """Eine Hero-Section (Kacheln) rendern; bei list/activity zusätzlich Tages-Pills."""
    st.subheader(sec["title"])
    _tiles(sec["metrics"])
    if sec.get("list") is None and sec.get("activity") is None:
        return
    bookings = sec.get("list") or []
    activity = sec.get("activity") or []
    today_d = datetime.now().date()
    day_opts = [today_d - timedelta(days=i) for i in range(7)]

    def _day_label(d):
        i = (today_d - d).days
        return ("Heute" if i == 0 else "Gestern" if i == 1
                else f"{_WD[d.weekday()]} {d.strftime('%d.%m.')}")

    # Default = jüngster Tag MIT Aktivität (sonst öffnet der Tab leer, obwohl gestern
    # voll ist). day_opts ist absteigend → erster Treffer = aktuellster aktiver Tag.
    active = {it.get("date") for it in bookings} | {a.get("date") for a in activity}
    default_day = next((d for d in day_opts if d.isoformat() in active), today_d)

    # Kompakter Tag-Wähler: st.pills wrappt horizontal (statt 7 Buttons am Handy vertikal
    # zu stapeln). Eigener State über key; kein st.rerun() → aktiver Tab bleibt erhalten.
    chosen = st.pills("Tag wählen", day_opts, selection_mode="single",
                      default=default_day, format_func=_day_label,
                      label_visibility="collapsed", key="sel_day_pill")
    sel = (chosen or default_day).isoformat()
    # Leere Tage: nichts anzeigen (keine „Ruhiger Tag"-Meldung).
    if sec.get("list") is not None:
        day_items = [it for it in bookings if it.get("date") == sel]
        if day_items:
            st.markdown(_booking_list(day_items), unsafe_allow_html=True)
    if sec.get("activity") is not None:
        st.markdown("**🗒️ Postfach-Aktivität**")
        day_act = [a for a in activity if a.get("date") == sel]
        if day_act:
            st.markdown(_activity_list(day_act), unsafe_allow_html=True)
        else:
            st.caption("Keine Aktivität.")


def _section(h, needle):
    """Hero-Section anhand eines Titel-Stichworts finden."""
    if not h:
        return None
    return next((s for s in h.hero_sections if needle in s["title"]), None)


hero = next((r for r in results if r.category == Category.HEUTE), None)

tab_heute, tab_vorgaenge, tab_einnahmen, tab_reichweite = st.tabs(
    ["🎯 Heute", "📋 Vorgänge", "💶 Einnahmen", "📣 Reichweite"])

# --- 🎯 Heute: der tägliche Blick – Erfolgs-Bänder + Tageseinnahmen ---
with tab_heute:
    if hero and hero.ok and hero.bands:
        # „Erfolg heute" groß als Held, gestern/7T/30T als kompakte Chips darunter
        # → alle vier Zahlen auf einen Blick, auch am Handy.
        st.markdown(_band(hero.bands[0]), unsafe_allow_html=True)
        if len(hero.bands) > 1:
            st.markdown(_chips(hero.bands[1:]), unsafe_allow_html=True)
        # Festbuchungen = Kerngeschäft → prominent direkt unter den Erfolgs-Kacheln
        # (Buchungsprovision heute links, dann Festbuchungen heute/Monat)
        bsec = _section(hero, "Buchungen")
        if bsec:
            _want = ["Buchungsprovision heute", "Festbuchungen heute",
                     "Neue Optionen heute", "Festbuchungen Monat"]
            festb = [m for w in _want for m in bsec["metrics"] if m.label == w]
            if festb:
                _tiles(festb)
        # Reichweite als kompakte Mini-Leiste
        gsec = _section(hero, "Accountwachstum")
        if gsec and gsec.get("metrics"):
            st.markdown(_growth_strip(gsec["metrics"]), unsafe_allow_html=True)
        sec = _section(hero, "Tageseinnahmen")
        if sec:
            _render_section(sec)
    elif hero:
        render_group(hero)

# --- 📋 Vorgänge: Buchungen/Optionen + Postfach-Aktivität (operatives Detail) ---
with tab_vorgaenge:
    sec = _section(hero, "Buchungen")
    if sec:
        _render_section(sec)
    else:
        st.caption("Keine Vorgangsdaten verfügbar.")

# --- 💶 Einnahmen: fakturiert (Lexware) + Monats-/Jahresdetail + Pipeline ---
with tab_einnahmen:
    sec = _section(hero, "fakturiert")
    if sec:
        _render_section(sec)
    for category in (Category.EINNAHMEN, Category.PIPELINE):
        group = [r for r in results if r.category == category]
        if not group:
            continue
        st.subheader(f"{CATEGORY_ICON[category]} {category.value}")
        for res in group:
            render_group(res)

# --- 📣 Reichweite: Detail-Karten (Accountwachstum-Kompaktleiste sitzt im Heute-Tab) ---
with tab_reichweite:
    vanity = [r for r in results if r.category == Category.VANITY]
    kit_r = next((r for r in vanity if "Morrletter" in r.name), None)
    bc_r = next((r for r in vanity if r.name == "Letzte Aussendung"), None)
    yt_r = next((r for r in vanity if r.name.startswith("YouTube")), None)

    # 📧 Morrletter: Wachstum + letzte Aussendung in EINEM Block (nicht separat)
    if kit_r and kit_r.ok:
        st.subheader("📧 Morrletter")
        metrics = list(kit_r.metrics) + (list(bc_r.metrics) if (bc_r and bc_r.ok) else [])
        _tiles(metrics)
        if kit_r.caption:
            st.caption(kit_r.caption)
        if bc_r and bc_r.ok and bc_r.caption:
            st.caption("📨 Letzte Aussendung · " + bc_r.caption)
    elif kit_r:
        render_group(kit_r)

    # ▶️ YouTube (Abonnenten exakt, Aufrufe, Videos)
    if yt_r:
        render_group(yt_r)

st.caption("morr.de · Daily Morr – lokaler Erfolgs-Überblick (Phase 1)")
