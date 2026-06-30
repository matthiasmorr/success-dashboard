"""Erfolgs-Dashboard – strategischer Wachstums-Überblick für morr.de.

Phase 1 (MVP, lokal): Einnahmen (Digistore, Awin) + Reichweite (YouTube, KIT) per API.
Start:  ./venv/bin/streamlit run app.py

Präsentation: „Command-Center"-Redesign (Richtung 1c aus dem Design-Handoff) –
linke Seitennavigation, weiße Karten mit Mini-Trendlinien (Sparklines), großer
„Erfolg heute"-Held. Daten/Connectoren/Logik unverändert – nur die Render-Helfer.
"""
from __future__ import annotations

import html
import json
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

# --- morr.de-Branding + Command-Center-Redesign (Design-Tokens aus dem Handoff) ---
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..700&family=Lato:wght@400;700;900&display=swap');
    html, body, [class*="st-"], .stMarkdown { font-family: 'Lato', sans-serif; }
    h1, h2, h3, h4 { font-family: 'Fraunces', serif !important; color: #1B1B6D; letter-spacing: -0.01em; }
    /* Hauptfläche im hellen Lila-Panel-Ton */
    .stApp { background: #f6f5fc; }
    .block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1200px; }

    /* ===================== Sidebar = Command-Center-Navigation ===================== */
    section[data-testid="stSidebar"] { background: #1B1B6D; }
    section[data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }
    .mm-brand { display:flex; align-items:center; gap:10px; padding:2px 6px 16px; }
    .mm-brand svg { width:30px; height:30px; display:block; }
    .mm-brand span { font-family:'Fraunces',serif; font-weight:600; font-size:1.15rem; color:#fff; }
    /* st.radio als Navigations-Liste */
    section[data-testid="stSidebar"] [role="radiogroup"] { gap:5px; }
    section[data-testid="stSidebar"] [role="radiogroup"] > label {
        padding:10px 12px; border-radius:10px; margin:0; width:100%; transition:background .12s; }
    section[data-testid="stSidebar"] [role="radiogroup"] > label > div:first-child { display:none; }  /* Radiopunkt aus */
    section[data-testid="stSidebar"] [role="radiogroup"] > label div[data-testid="stMarkdownContainer"] p {
        color:#b9b9e8; font-weight:700; font-size:.92rem; }
    section[data-testid="stSidebar"] [role="radiogroup"] > label:hover { background:rgba(255,255,255,.07); }
    section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) { background:#3636D9; }
    section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked)
        div[data-testid="stMarkdownContainer"] p { color:#fff; }
    .mm-foot { color:#9a9ad8; font-size:.72rem; line-height:1.55; padding:12px 8px 0;
        border-top:1px solid rgba(255,255,255,.13); margin-top:14px; }
    .mm-foot b { color:#b9b9e8; font-weight:700; }
    .mm-foot .sail { display:inline-block; animation:morr-sail 1.4s ease-in-out infinite; transform-origin:50% 80%; }

    /* ===================== Topbar ===================== */
    .mm-title { font-family:'Fraunces',serif; font-weight:600; font-size:1.6rem; color:#1B1B6D;
        line-height:2.3rem; white-space:nowrap; }
    .stButton > button { border-radius:999px !important; padding:6px 14px !important;
        font-weight:700 !important; font-size:.82rem !important; }
    .stButton > button[kind="secondary"] { background:#fff !important; color:#5a5a86 !important;
        border:1px solid #e7e6f7 !important; }
    .stButton > button[kind="primary"] { background:#1B1B6D !important; color:#fff !important;
        border:none !important; box-shadow:0 3px 10px rgba(27,27,109,.22) !important; }

    /* ===================== Hero + Vergleichs-Karten ===================== */
    .mm-hero-row { display:flex; gap:16px; margin:4px 0 16px; flex-wrap:wrap; }
    .mm-hero { flex:1.7; min-width:230px; color:#fff; border-radius:18px; padding:22px 26px;
        background:linear-gradient(135deg,#1B1B6D 0%,#3636D9 100%); box-shadow:0 8px 26px rgba(27,27,109,.22); }
    .mm-hero-eyebrow { font-weight:700; text-transform:uppercase; letter-spacing:.13em;
        font-size:.74rem; color:#d4d3f4; }
    .mm-hero-value { font-family:'Fraunces',serif; font-weight:700; font-size:3.1rem; line-height:1;
        margin:6px 0 4px; white-space:nowrap; }
    .mm-hero-sub { color:#d4d3f4; font-size:.82rem; font-weight:700; }
    .mm-cmp { flex:1; min-width:104px; background:#fff; border:1px solid #e7e6f7; border-radius:18px;
        padding:16px 18px; display:flex; flex-direction:column; justify-content:center; }
    .mm-cmp-label { color:#9a9ac0; font-size:.72rem; text-transform:uppercase; letter-spacing:.1em;
        font-weight:700; margin-bottom:5px; }
    .mm-cmp-value { font-family:'Fraunces',serif; font-weight:600; font-size:1.55rem; color:#1B1B6D;
        white-space:nowrap; }
    .mm-cmp-sub { color:#9a9ac0; font-size:.72rem; margin-top:3px; }

    /* ===================== KPI-Karten-Raster ===================== */
    .mm-grid { display:grid; gap:12px; margin:0 0 16px; align-items:start; }
    .mm-grid-2 { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .mm-grid-3 { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .mm-grid-4 { grid-template-columns:repeat(4,minmax(0,1fr)); }
    .mm-grid-5 { grid-template-columns:repeat(5,minmax(0,1fr)); }
    @media (max-width: 860px){ .mm-grid-3,.mm-grid-4,.mm-grid-5 { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    .mm-card { background:#fff; border:1px solid #e7e6f7; border-radius:13px; padding:13px 15px;
        display:flex; flex-direction:column; min-height:92px; }
    .mm-card-top { display:flex; align-items:flex-start; justify-content:space-between; gap:6px; margin-bottom:6px; }
    .mm-kpi-label { color:#5a5a86; font-weight:700; font-size:.74rem; line-height:1.25; min-width:0;
        display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .mm-chip { flex:none; font-weight:700; font-size:.66rem; border-radius:999px; padding:1px 7px; white-space:nowrap; }
    .mm-card-bot { display:flex; align-items:flex-end; justify-content:space-between; gap:8px; margin-top:auto; }
    .mm-kpi-value { font-family:'Fraunces',serif; font-weight:700; font-size:1.4rem; color:#1B1B6D;
        line-height:1.1; white-space:nowrap; }
    .mm-kpi-sub { color:#9a9ac0; font-size:.72rem; margin-top:4px; line-height:1.3; }
    .mm-label { font-weight:700; color:#1B1B6D; font-size:.92rem; margin:16px 0 8px; }

    /* ===================== Reichweite-Wachstumsstreifen (nur Änderung, groß) ===================== */
    .grow-row { display:flex; gap:10px; margin:2px 0 18px; flex-wrap:wrap; }
    .grow-item { flex:1; min-width:96px; background:#fff; border:1px solid #e7e6f7;
        border-radius:12px; padding:10px 13px; }
    .grow-label { color:#5a5a86; font-size:.7rem; font-weight:700; line-height:1.2;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .grow-change { font-family:'Fraunces',serif; font-weight:700; font-size:1.55rem; line-height:1.2; margin-top:1px; }
    .grow-change.pos { color:#2f7a2f; }
    .grow-change.neg { color:#b23b3b; }
    .grow-change.zero { color:#8a8ab5; }
    .grow-spark { margin-top:4px; }

    /* ===================== Reichweite-Social-Karten (Detail) ===================== */
    .mm-soc-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin:0 0 16px; }
    @media (max-width: 860px){ .mm-soc-row { grid-template-columns:1fr; } }
    .mm-soc { background:#fff; border:1px solid #e7e6f7; border-radius:14px; padding:16px 18px;
        display:flex; align-items:center; justify-content:space-between; gap:12px; }
    .mm-soc-label { color:#5a5a86; font-size:.8rem; font-weight:700; }
    .mm-soc-value { font-family:'Fraunces',serif; font-weight:700; font-size:1.7rem; color:#1B1B6D;
        line-height:1.05; margin-top:2px; white-space:nowrap; }
    .mm-soc-delta { font-size:.78rem; font-weight:700; margin-top:3px; }

    /* ===================== Buchungs-/Optionsliste ===================== */
    .bk-list { margin: 4px 0 14px; background:#fff; border:1px solid #e7e6f7; border-radius:13px; overflow:hidden; }
    .bk-row { display:flex; align-items:center; gap:12px; padding:9px 16px; border-bottom:1px solid #f3f2fb; }
    .bk-row:last-child { border-bottom:none; }
    .bk-badge { font-size:.66rem; font-weight:700; padding:3px 10px; border-radius:999px;
        color:#fff; min-width:66px; text-align:center; letter-spacing:.02em; }
    .b-buchung { background:#1B1B6D; }              /* Indigo = feste Buchung (realisiert) */
    .b-option  { background:#D6D4F2; color:#1B1B6D; }  /* helles Lavendel = Option (vorläufig) */
    .bk-val { font-weight:700; color:#1B1B6D; min-width:100px; }
    .bk-name { font-weight:700; color:#1B1B6D; min-width:118px; }
    .bk-label { color:#5a5a86; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .bk-date { color:#9a9ac0; font-size:.82rem; }

    /* ===================== Postfach-Aktivität ===================== */
    .act-list { margin:4px 0 14px; display:flex; flex-direction:column; gap:8px; }
    .act-row { border:1px solid #e7e6f7; border-left:4px solid #3636D9; border-radius:11px;
        padding:11px 14px; background:#fff; }
    .act-problem { border-left-color:#d98a1f; background:#fff8ef; }
    .act-out { border-left-color:#3a8a3a; background:#f5fbf5; }   /* beantwortet */
    .act-flag { font-size:.64rem; font-weight:700; color:#2f7a2f; background:#e4f3e4;
        border-radius:999px; padding:1px 8px; }
    .act-head { display:flex; align-items:center; gap:8px; }
    .act-kontakt { font-weight:700; color:#1B1B6D; }
    .act-date { color:#9a9ac0; font-size:.82rem; }
    .act-betreff { color:#9a9ac0; font-size:.82rem; margin:2px 0 3px; }
    .act-text { color:#3a3a5a; font-size:.88rem; line-height:1.35; }
    .act-reply { color:#2f7a2f; font-weight:700; }

    /* Lauf-Indikator: Streamlits rennendes Männchen raus, maritimes Schiff rein */
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


def _logo(name: str) -> str:
    f = Path(__file__).with_name("assets") / name
    if not f.exists():
        return ""
    t = f.read_text(encoding="utf-8")
    return t[t.find("<svg"):]   # XML-Deklaration weglassen


_LOGO_WHITE = _logo("m-white.svg")


# ===================== Datenladen (unverändert) =====================
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


# ===================== Render-Bausteine =====================
TONE = {  # (Textfarbe, Sparkline-Stroke, Chip-Hintergrund)
    "pos":  ("#2f7a2f", "#2f9e2f", "#e4f3e4"),
    "neg":  ("#b23b3b", "#d05858", "#fbe7e7"),
    "neut": ("#5a5a86", "#3636D9", "#eceaf6"),
}
_SOCIAL_HISTORY = Path(__file__).with_name("data") / "social_history.json"
_SOCIAL_KEYS = {"YouTube": "youtube", "Instagram": "instagram",
                "Facebook": "facebook", "TikTok": "tiktok"}


def _social_series(platform: str | None, n: int = 14):
    """Letzte ~n Tageswerte einer Plattform aus dem Snapshot-Verlauf (für Sparklines).

    None, wenn keine verwertbare Historie (≥2 Punkte) vorliegt – dann zeigen wir
    bewusst keine Sparkline statt einer Flatline (siehe Handoff).
    """
    if not platform:
        return None
    try:
        hist = json.loads(_SOCIAL_HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    days = sorted(d for d in hist if isinstance(hist.get(d), dict))
    vals = [hist[d][platform] for d in days if isinstance(hist[d].get(platform), int)]
    vals = vals[-n:]
    return vals if len(vals) >= 2 else None


def _spark_svg(vals, stroke, w_css=64, h_css=22, width=100, height=30, pad=3):
    if not vals or len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(vals)
    pts = " ".join(
        f"{(0 if n == 1 else i / (n - 1)) * width:.2f},"
        f"{pad + (1 - (v - lo) / span) * (height - 2 * pad):.2f}"
        for i, v in enumerate(vals))
    return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
            f'style="width:{w_css}px;height:{h_css}px">'
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="2.4" '
            f'stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>')


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


def _tone_of_change(change):
    if not change:
        return "neut"
    digits = change.lstrip("+-−± ")
    if digits in ("", "0") or set(digits) <= {"0"}:
        return "neut"
    return "neg" if change[:1] in ("-", "−") else "pos"


def _chip_tone(delta):
    """Tonalität eines Delta-Chips: Wachstum grün/rot, Kontext (7,5 %, Monat …) neutral."""
    s = str(delta or "").strip()
    if not s:
        return "neut"
    if s[:1] in ("+", "±"):
        return _tone_of_change(s)
    if s[:1] in ("-", "−"):
        return "neg"
    return "neut"


def _kpi_card(label, value, delta=None, tone="neut", vals=None):
    """Weiße KPI-Karte: Label + Delta (kurz=Chip, lang=Subzeile) + Wert + optionale Sparkline."""
    text, stroke, chip = TONE[tone]
    spark = _spark_svg(vals, stroke) if vals else ""
    s = "" if delta in (None, "") else str(delta)
    chiplike = bool(s) and len(s) <= 14 and "·" not in s and "Monat" not in s and "Gestern" not in s
    chip_html = (f'<span class="mm-chip" style="color:{text};background:{chip}">{html.escape(s)}</span>'
                 if chiplike else "")
    sub_html = f'<div class="mm-kpi-sub">{html.escape(s)}</div>' if (s and not chiplike) else ""
    return (
        f'<div class="mm-card">'
        f'<div class="mm-card-top"><span class="mm-kpi-label">{html.escape(str(label))}</span>{chip_html}</div>'
        f'<div class="mm-card-bot"><div class="mm-kpi-value">{html.escape(str(value))}</div>{spark}</div>'
        f'{sub_html}</div>')


def _grid(metrics, cols=4):
    """Eine Reihe weißer KPI-Karten als CSS-Grid rendern."""
    if not metrics:
        return
    cards = "".join(_kpi_card(m.label, m.value, m.delta, _chip_tone(m.delta)) for m in metrics)
    st.markdown(f'<div class="mm-grid mm-grid-{cols}">{cards}</div>', unsafe_allow_html=True)


def _label(txt):
    st.markdown(f'<div class="mm-label">{html.escape(str(txt))}</div>', unsafe_allow_html=True)


def render_group(res):
    """Fallback-Renderer für eigenständige Connector-Ergebnisse (Einnahmen/Pipeline)."""
    if res.ok and res.metrics:
        _label(res.name)
        _grid(res.metrics)
        if res.caption:
            st.caption(res.caption)
    elif not res.configured:
        st.info(f"**{res.name}** – noch nicht eingerichtet: {res.error}", icon="⚙️")
    else:
        st.warning(f"**{res.name}** – Fehler beim Abruf: {res.error}", icon="⚠️")


def _hero_row(bands):
    """Held „Erfolg heute" + 3 Vergleichskarten (gestern · 7 T · 30 T) in einer Reihe."""
    hero = bands[0]
    cells = [
        f'<div class="mm-hero" title="{html.escape(hero.get("help", ""))}">'
        f'<div class="mm-hero-eyebrow">{html.escape(hero["label"])}</div>'
        f'<div class="mm-hero-value">{hero["value"]}</div>'
        f'<div class="mm-hero-sub">{html.escape(hero.get("sub", ""))}</div></div>'
    ]
    for b in bands[1:]:
        label = b["label"].replace("Erfolg ", "")
        # Cents in den Vergleichskarten weglassen (glanceable) – volle Präzision im Held.
        val = b["value"].split(",")[0] + " €" if "," in b["value"] else b["value"]
        cells.append(
            f'<div class="mm-cmp" title="{html.escape(b.get("help", ""))}">'
            f'<div class="mm-cmp-label">{html.escape(label)}</div>'
            f'<div class="mm-cmp-value">{val}</div>'
            f'<div class="mm-cmp-sub">{html.escape(b.get("sub", ""))}</div></div>')
    st.markdown('<div class="mm-hero-row">' + "".join(cells) + "</div>", unsafe_allow_html=True)


def _growth_strip(metrics):
    """Heute-Reichweite kompakt: je Account NUR die Änderung seit gestern – groß,
    darunter eine kleine Trendlinie (wo Verlauf vorliegt)."""
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
        stroke = {"pos": "#2f9e2f", "neg": "#d05858", "zero": "#9a9ad8"}[sign]
        vals = _social_series(_SOCIAL_KEYS.get(m.label))
        spark = (f'<div class="grow-spark">{_spark_svg(vals, stroke, w_css=104, h_css=20)}</div>'
                 if vals else "")
        cells.append(
            f'<div class="grow-item" title="{html.escape(m.help or "")}">'
            f'<div class="grow-label">{html.escape(m.label)}</div>'
            f'<div class="grow-change {sign}">{html.escape(big)}</div>'
            f'{spark}</div>')
    st.markdown('<div class="grow-row">' + "".join(cells) + "</div>", unsafe_allow_html=True)


def _social_cards(metrics):
    """Reichweite-Detail: pro Account Karte mit absoluter Zahl, Änderung und breiter Sparkline."""
    cards = []
    for m in metrics:
        change = _change_of(m)
        tone = _tone_of_change(change)
        text, stroke, _chip = TONE[tone]
        vals = _social_series(_SOCIAL_KEYS.get(m.label))
        spark = _spark_svg(vals, stroke, w_css=108, h_css=42) if vals else ""
        delta_html = (f'<div class="mm-soc-delta" style="color:{text}">{html.escape(change)} seit gestern</div>'
                      if change else "")
        cards.append(
            f'<div class="mm-soc" title="{html.escape(m.help or "")}"><div>'
            f'<div class="mm-soc-label">{html.escape(m.label)}</div>'
            f'<div class="mm-soc-value">{html.escape(str(m.value))}</div>'
            f'{delta_html}</div>{spark}</div>')
    st.markdown('<div class="mm-soc-row">' + "".join(cards) + "</div>", unsafe_allow_html=True)


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


def _day_lists(bookings, activity, show_activity=True):
    """Tages-Wähler (Pills) + Buchungsliste + Postfach-Aktivität für den gewählten Tag."""
    bookings = bookings or []
    activity = activity or []
    today_d = datetime.now().date()
    day_opts = [today_d - timedelta(days=i) for i in range(7)]

    def _day_label(d):
        i = (today_d - d).days
        return ("Heute" if i == 0 else "Gestern" if i == 1
                else f"{_WD[d.weekday()]} {d.strftime('%d.%m.')}")

    # Default = jüngster Tag MIT Aktivität (sonst öffnet die Seite leer, obwohl gestern voll ist).
    active = {it.get("date") for it in bookings} | {a.get("date") for a in activity}
    default_day = next((d for d in day_opts if d.isoformat() in active), today_d)
    chosen = st.pills("Tag wählen", day_opts, selection_mode="single",
                      default=default_day, format_func=_day_label,
                      label_visibility="collapsed", key="sel_day_pill")
    sel = (chosen or default_day).isoformat()

    day_items = [it for it in bookings if it.get("date") == sel]
    if day_items:
        st.markdown(_booking_list(day_items), unsafe_allow_html=True)
    if show_activity:
        _label("🗒️ Postfach-Aktivität")
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


# ===================== Layout: Sidebar-Navigation + Topbar =====================
NAV = ["🎯 Heute", "📋 Vorgänge", "💶 Einnahmen", "📣 Reichweite"]
CATEGORY_ICON = {Category.EINNAHMEN: "💶", Category.PIPELINE: "📨"}

with st.sidebar:
    st.markdown(f'<div class="mm-brand">{_LOGO_WHITE}<span>Daily Morr</span></div>',
                unsafe_allow_html=True)
    nav = st.radio("Bereich", NAV, label_visibility="collapsed", key="nav")

# Topbar: Bereichstitel + die beiden Aktualisieren-Aktionen (rechts)
t_title, t_b1, t_b2 = st.columns([5, 1.5, 1.7])
with t_title:
    st.markdown(f'<div class="mm-title">{nav}</div>', unsafe_allow_html=True)
with t_b1:
    if st.button("🔄 Aktualisieren", use_container_width=True,
                 help="Holt den neuesten Hintergrund-Stand (wird alle 30 Min frisch "
                      "gerechnet) – ein paar Sekunden."):
        load_all.clear()
        st.session_state["_mode"] = "drive"
        st.rerun()
with t_b2:
    if st.button("🐢 Neu rechnen", type="primary", use_container_width=True,
                 help="Rechnet alle Quellen direkt neu. In der Cloud einige Minuten – "
                      "nur nötig, wenn etwas ganz Frisches sofort erscheinen soll."):
        load_all.clear()
        st.session_state["_mode"] = "live"
        st.rerun()

results, _data_ts = load_all(st.session_state.pop("_mode", "auto"))
_stand = datetime.fromtimestamp(_data_ts).strftime("%d.%m. · %H:%M") if _data_ts else "—"
st.sidebar.markdown(
    f'<div class="mm-foot"><b>morr.de</b><br>Stand {_stand}<br>'
    f'<span class="sail">🚢</span> alle Quellen live</div>',
    unsafe_allow_html=True,
)

hero = next((r for r in results if r.category == Category.HEUTE), None)


# ===================== 🎯 Heute =====================
if nav == "🎯 Heute":
    if hero and hero.ok and hero.bands:
        _hero_row(hero.bands)
        # Festbuchungen = Kerngeschäft → prominent direkt unter dem Held
        bsec = _section(hero, "Buchungen")
        if bsec:
            _want = ["Buchungsprovision heute", "Festbuchungen heute",
                     "Neue Optionen heute", "Festbuchungen Monat"]
            festb = [m for w in _want for m in bsec["metrics"] if m.label == w]
            _grid(festb, cols=4)
        # Reichweite kompakt: nur die Änderung, groß (+ Mini-Trend)
        gsec = _section(hero, "Accountwachstum")
        if gsec and gsec.get("metrics"):
            _growth_strip(gsec["metrics"])
        # Tageseinnahmen
        tsec = _section(hero, "Tageseinnahmen")
        if tsec and tsec.get("metrics"):
            _label("💶 Tageseinnahmen")
            _grid(tsec["metrics"], cols=4)
    elif hero:
        render_group(hero)
    else:
        st.info("Noch keine Daten – auf „Neu rechnen“ tippen.", icon="🚢")

# ===================== 📋 Vorgänge =====================
elif nav == "📋 Vorgänge":
    bsec = _section(hero, "Buchungen")
    if bsec:
        _grid(bsec["metrics"], cols=4)
        _label("📋 Buchungen & Optionen")
        _day_lists(bsec.get("list"), bsec.get("activity"))
    else:
        st.caption("Keine Vorgangsdaten verfügbar.")

# ===================== 💶 Einnahmen =====================
elif nav == "💶 Einnahmen":
    lsec = _section(hero, "fakturiert")
    if lsec and lsec.get("metrics"):
        _label(lsec["title"])
        _grid(lsec["metrics"], cols=4)
    for category in (Category.EINNAHMEN, Category.PIPELINE):
        group = [r for r in results if r.category == category]
        if not group:
            continue
        _label(f"{CATEGORY_ICON[category]} {category.value}")
        for res in group:
            render_group(res)

# ===================== 📣 Reichweite =====================
elif nav == "📣 Reichweite":
    gsec = _section(hero, "Accountwachstum")
    if gsec and gsec.get("metrics"):
        _social_cards(gsec["metrics"])

    vanity = [r for r in results if r.category == Category.VANITY]
    kit_r = next((r for r in vanity if "Morrletter" in r.name), None)
    bc_r = next((r for r in vanity if r.name == "Letzte Aussendung"), None)
    yt_r = next((r for r in vanity if r.name.startswith("YouTube")), None)

    # 📧 Morrletter: Wachstum + letzte Aussendung in EINEM Block
    if kit_r and kit_r.ok:
        _label("📧 Morrletter")
        metrics = list(kit_r.metrics) + (list(bc_r.metrics) if (bc_r and bc_r.ok) else [])
        _grid(metrics, cols=4)
        if kit_r.caption:
            st.caption(kit_r.caption)
        if bc_r and bc_r.ok and bc_r.caption:
            st.caption("📨 Letzte Aussendung · " + bc_r.caption)
    elif kit_r:
        render_group(kit_r)

    # ▶️ YouTube (Abonnenten exakt, Aufrufe, Videos)
    if yt_r and yt_r.ok:
        _label("▶️ YouTube")
        _grid(yt_r.metrics, cols=4)
        if yt_r.caption:
            st.caption(yt_r.caption)
    elif yt_r:
        render_group(yt_r)
