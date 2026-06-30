"""Erfolgs-Dashboard – strategischer Wachstums-Überblick für morr.de.

Phase 1 (MVP, lokal): Einnahmen (Digistore, Awin) + Reichweite (YouTube, KIT) per API.
Start:  ./venv/bin/streamlit run app.py

Präsentation: „Command-Center"-Redesign (Richtung 1c aus dem Design-Handoff) als EINE
durchgehende HTML-Seite (eigene Sidebar, Topbar, Karten am Stück) – Streamlit dient nur
als geschützte Hülle + Python-Runtime. Navigation/Tagewahl/Aktualisieren laufen über die
URL-Query (`?nav=…&day=…&do=…`) statt über Streamlit-Widgets. Daten/Connectoren/Logik
unverändert – nur die Render-Schicht.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

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
# apple-touch-icon nur aus dem <head> – per JS in window.parent.document.head injizieren.
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

# --- Design-Tokens (Handoff, Richtung 1c) + Streamlit-Chrome ausblenden ---
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..700&family=Lato:wght@400;700;900&display=swap');

    /* Streamlit-Gerüst neutralisieren – die Seite gehört unserem HTML-Block */
    header[data-testid="stHeader"], #MainMenu, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="stStatusWidget"], footer { display:none !important; }
    [data-testid="stSidebar"] { display:none !important; }
    .stApp { background:#1B1B6D; }
    .block-container, [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"],
    [data-testid="stMain"] .block-container { padding:0 !important; max-width:100% !important; }
    [data-testid="stMain"] { padding:0 !important; }
    [data-testid="stVerticalBlock"] { gap:0 !important; }
    [data-testid="stHtml"] { margin:0 !important; }
    html, body { font-family:'Lato',system-ui,sans-serif; color:#1B1B6D; }

    /* ===================== Grundgerüst: Sidebar + Hauptbereich ===================== */
    .mm-shell { display:flex; min-height:100vh; align-items:stretch; background:#f6f5fc; }
    .mm-side { width:216px; flex:none; background:#1B1B6D; padding:26px 18px;
        display:flex; flex-direction:column; position:sticky; top:0; align-self:flex-start; min-height:100vh; }
    .mm-main { flex:1; min-width:0; padding:24px 30px 44px; }
    @media (max-width: 760px) {
        .mm-shell { flex-direction:column; }
        .mm-side { width:auto; min-height:0; position:static; flex-direction:row; flex-wrap:wrap;
            align-items:center; gap:6px; padding:12px 14px; }
        .mm-foot { display:none; }
        .mm-main { padding:16px 16px 32px; }
    }

    /* Sidebar-Inhalt */
    .mm-brand { display:flex; align-items:center; gap:10px; padding:2px 6px 18px; }
    .mm-brand svg { width:30px; height:30px; display:block; }
    .mm-brand span { font-family:'Fraunces',serif; font-weight:600; font-size:1.15rem; color:#fff; }
    .mm-nav { display:block; padding:11px 13px; border-radius:10px; color:#b9b9e8; font-weight:700;
        font-size:.9rem; text-decoration:none; margin-bottom:5px; transition:background .12s; }
    .mm-nav:hover { background:rgba(255,255,255,.07); color:#fff; }
    .mm-nav.active { background:#3636D9; color:#fff; }
    .mm-foot { margin-top:auto; color:#9a9ad8; font-size:.72rem; line-height:1.55; padding-top:14px;
        border-top:1px solid rgba(255,255,255,.13); }
    .mm-foot b { color:#b9b9e8; font-weight:700; }
    .mm-foot .sail { display:inline-block; animation:morr-sail 1.4s ease-in-out infinite; transform-origin:50% 80%; }

    /* Topbar */
    .mm-topbar { display:flex; align-items:center; justify-content:space-between; gap:12px;
        margin-bottom:18px; flex-wrap:wrap; }
    .mm-title { font-family:'Fraunces',serif; font-weight:600; font-size:1.6rem; color:#1B1B6D; white-space:nowrap; }
    .mm-actions { display:flex; gap:8px; }
    .mm-btn { text-decoration:none; border-radius:999px; padding:7px 15px; font-weight:700; font-size:.82rem;
        background:#fff; color:#5a5a86; border:1px solid #e7e6f7; white-space:nowrap; }
    .mm-btn:hover { border-color:#cfcdee; }
    .mm-btn-primary { background:#1B1B6D; color:#fff; border:none; box-shadow:0 3px 10px rgba(27,27,109,.22); }

    /* ===================== Held + Vergleichskarten ===================== */
    .mm-hero-row { display:flex; gap:16px; margin:4px 0 16px; flex-wrap:wrap; }
    .mm-hero { flex:1.7; min-width:230px; color:#fff; border-radius:18px; padding:22px 26px;
        background:linear-gradient(135deg,#1B1B6D 0%,#3636D9 100%); box-shadow:0 8px 26px rgba(27,27,109,.22); }
    .mm-hero-eyebrow { font-weight:700; text-transform:uppercase; letter-spacing:.13em; font-size:.74rem; color:#d4d3f4; }
    .mm-hero-value { font-family:'Fraunces',serif; font-weight:700; font-size:3.1rem; line-height:1;
        margin:6px 0 4px; white-space:nowrap; }
    .mm-hero-sub { color:#d4d3f4; font-size:.82rem; font-weight:700; }
    .mm-cmp { flex:1; min-width:104px; background:#fff; border:1px solid #e7e6f7; border-radius:18px;
        padding:16px 18px; display:flex; flex-direction:column; justify-content:center; }
    .mm-cmp-label { color:#9a9ac0; font-size:.72rem; text-transform:uppercase; letter-spacing:.1em;
        font-weight:700; margin-bottom:5px; }
    .mm-cmp-value { font-family:'Fraunces',serif; font-weight:600; font-size:1.55rem; color:#1B1B6D; white-space:nowrap; }
    .mm-cmp-sub { color:#9a9ac0; font-size:.72rem; margin-top:3px; }

    /* ===================== KPI-Karten-Raster ===================== */
    .mm-grid { display:grid; gap:12px; margin:0 0 16px; align-items:start; }
    .mm-grid-2 { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .mm-grid-3 { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .mm-grid-4 { grid-template-columns:repeat(4,minmax(0,1fr)); }
    .mm-grid-5 { grid-template-columns:repeat(5,minmax(0,1fr)); }
    @media (max-width: 980px){ .mm-grid-5 { grid-template-columns:repeat(4,minmax(0,1fr)); } }
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
    .mm-cap { color:#9a9ac0; font-size:.78rem; margin:-6px 0 12px; line-height:1.4; }
    .mm-empty { color:#9a9ac0; font-size:.82rem; margin:2px 0 12px; }
    .mm-info { background:#fff; border:1px solid #e7e6f7; border-left:4px solid #3636D9; border-radius:11px;
        padding:11px 14px; color:#5a5a86; font-size:.86rem; margin:4px 0 12px; }
    .mm-info.warn { border-left-color:#d98a1f; background:#fff8ef; }

    /* ===================== Wachstumsstreifen (nur Änderung, groß) ===================== */
    .grow-row { display:flex; gap:10px; margin:2px 0 18px; flex-wrap:wrap; }
    .grow-item { flex:1; min-width:96px; background:#fff; border:1px solid #e7e6f7; border-radius:12px; padding:10px 13px; }
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

    /* ===================== Tages-Pills ===================== */
    .mm-pills { display:flex; gap:7px; flex-wrap:wrap; margin:4px 0 14px; }
    .mm-pill { text-decoration:none; border-radius:999px; padding:5px 13px; font-weight:700; font-size:.82rem;
        background:#fff; color:#5a5a86; border:1px solid #e7e6f7; white-space:nowrap; }
    .mm-pill:hover { border-color:#cfcdee; }
    .mm-pill.active { color:#1B1B6D; border-color:#3636D9; }

    /* ===================== Buchungs-/Optionsliste ===================== */
    .bk-list { margin:4px 0 14px; background:#fff; border:1px solid #e7e6f7; border-radius:13px; overflow:hidden; }
    .bk-row { display:flex; align-items:center; gap:12px; padding:9px 16px; border-bottom:1px solid #f3f2fb; }
    .bk-row:last-child { border-bottom:none; }
    .bk-badge { font-size:.66rem; font-weight:700; padding:3px 10px; border-radius:999px;
        color:#fff; min-width:66px; text-align:center; letter-spacing:.02em; }
    .b-buchung { background:#1B1B6D; }
    .b-option  { background:#D6D4F2; color:#1B1B6D; }
    .bk-val { font-weight:700; color:#1B1B6D; min-width:100px; }
    .bk-name { font-weight:700; color:#1B1B6D; min-width:118px; }
    .bk-label { color:#5a5a86; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .bk-date { color:#9a9ac0; font-size:.82rem; }

    /* ===================== Postfach-Aktivität ===================== */
    .act-list { margin:4px 0 14px; display:flex; flex-direction:column; gap:8px; }
    .act-row { border:1px solid #e7e6f7; border-left:4px solid #3636D9; border-radius:11px; padding:11px 14px; background:#fff; }
    .act-problem { border-left-color:#d98a1f; background:#fff8ef; }
    .act-out { border-left-color:#3a8a3a; background:#f5fbf5; }
    .act-flag { font-size:.64rem; font-weight:700; color:#2f7a2f; background:#e4f3e4; border-radius:999px; padding:1px 8px; }
    .act-head { display:flex; align-items:center; gap:8px; }
    .act-kontakt { font-weight:700; color:#1B1B6D; }
    .act-date { color:#9a9ac0; font-size:.82rem; }
    .act-betreff { color:#9a9ac0; font-size:.82rem; margin:2px 0 3px; }
    .act-text { color:#3a3a5a; font-size:.88rem; line-height:1.35; }
    .act-reply { color:#2f7a2f; font-weight:700; }

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
        results = _compute_all()
        snapshot.save(results)
        return results, time.time()

    # auto
    snap, ts = snapshot.load(max_age_min=45)
    if snap is not None:
        return snap, ts
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


# ===================== HTML-Bausteine =====================
def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


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

    None, wenn keine verwertbare Historie (≥2 Punkte) – dann zeigen wir bewusst keine
    Sparkline statt einer Flatline.
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
    if v[:1] in ("+", "-", "−", "±"):
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
    s = str(delta or "").strip()
    if not s:
        return "neut"
    if s[:1] in ("+", "±"):
        return _tone_of_change(s)
    if s[:1] in ("-", "−"):
        return "neg"
    return "neut"


def _kpi_card(label, value, delta=None, tone="neut", vals=None):
    text, stroke, chip = TONE[tone]
    spark = _spark_svg(vals, stroke) if vals else ""
    s = "" if delta in (None, "") else str(delta)
    chiplike = bool(s) and len(s) <= 14 and "·" not in s and "Monat" not in s and "Gestern" not in s
    chip_html = (f'<span class="mm-chip" style="color:{text};background:{chip}">{esc(s)}</span>'
                 if chiplike else "")
    sub_html = f'<div class="mm-kpi-sub">{esc(s)}</div>' if (s and not chiplike) else ""
    return (
        f'<div class="mm-card">'
        f'<div class="mm-card-top"><span class="mm-kpi-label">{esc(label)}</span>{chip_html}</div>'
        f'<div class="mm-card-bot"><div class="mm-kpi-value">{esc(value)}</div>{spark}</div>'
        f'{sub_html}</div>')


def _grid_html(metrics, cols=4):
    if not metrics:
        return ""
    cards = "".join(_kpi_card(m.label, m.value, m.delta, _chip_tone(m.delta)) for m in metrics)
    return f'<div class="mm-grid mm-grid-{cols}">{cards}</div>'


def _label_html(txt):
    return f'<div class="mm-label">{esc(txt)}</div>'


def _cap_html(txt):
    return f'<div class="mm-cap">{esc(txt)}</div>'


def _group_html(res):
    """Eigenständiges Connector-Ergebnis (Einnahmen/Pipeline) als HTML."""
    if res.ok and res.metrics:
        out = _label_html(res.name) + _grid_html(res.metrics, 4)
        if res.caption:
            out += _cap_html(res.caption)
        return out
    if not res.configured:
        return f'<div class="mm-info">⚙️ <b>{esc(res.name)}</b> – noch nicht eingerichtet: {esc(res.error)}</div>'
    return f'<div class="mm-info warn">⚠️ <b>{esc(res.name)}</b> – Fehler beim Abruf: {esc(res.error)}</div>'


def _hero_html(bands):
    """Held „Erfolg heute" + 3 Vergleichskarten (gestern · 7 T · 30 T) in einer Reihe."""
    hero = bands[0]
    cells = [
        f'<div class="mm-hero" title="{esc(hero.get("help", ""))}">'
        f'<div class="mm-hero-eyebrow">{esc(hero["label"])}</div>'
        f'<div class="mm-hero-value">{hero["value"]}</div>'
        f'<div class="mm-hero-sub">{esc(hero.get("sub", ""))}</div></div>'
    ]
    for b in bands[1:]:
        label = b["label"].replace("Erfolg ", "")
        val = b["value"].split(",")[0] + " €" if "," in b["value"] else b["value"]
        cells.append(
            f'<div class="mm-cmp" title="{esc(b.get("help", ""))}">'
            f'<div class="mm-cmp-label">{esc(label)}</div>'
            f'<div class="mm-cmp-value">{val}</div>'
            f'<div class="mm-cmp-sub">{esc(b.get("sub", ""))}</div></div>')
    return '<div class="mm-hero-row">' + "".join(cells) + "</div>"


def _growth_html(metrics):
    """Heute-Reichweite kompakt: je Account NUR die Änderung seit gestern – groß + Mini-Trend."""
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
            f'<div class="grow-item" title="{esc(m.help or "")}">'
            f'<div class="grow-label">{esc(m.label)}</div>'
            f'<div class="grow-change {sign}">{esc(big)}</div>'
            f'{spark}</div>')
    return '<div class="grow-row">' + "".join(cells) + "</div>"


def _social_html(metrics):
    """Reichweite-Detail: pro Account Karte mit absoluter Zahl, Änderung und breiter Sparkline."""
    cards = []
    for m in metrics:
        change = _change_of(m)
        tone = _tone_of_change(change)
        text, stroke, _chip = TONE[tone]
        vals = _social_series(_SOCIAL_KEYS.get(m.label))
        spark = _spark_svg(vals, stroke, w_css=108, h_css=42) if vals else ""
        delta_html = (f'<div class="mm-soc-delta" style="color:{text}">{esc(change)} seit gestern</div>'
                      if change else "")
        cards.append(
            f'<div class="mm-soc" title="{esc(m.help or "")}"><div>'
            f'<div class="mm-soc-label">{esc(m.label)}</div>'
            f'<div class="mm-soc-value">{esc(m.value)}</div>'
            f'{delta_html}</div>{spark}</div>')
    return '<div class="mm-soc-row">' + "".join(cards) + "</div>"


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
        opt = all(x.get("art") == "option" for x in g)
        badge = "Option" if opt else "Buchung"
        cls = "b-option" if opt else "b-buchung"
        total = sum(x.get("value", 0) or 0 for x in g)
        iso = max((x.get("date", "") for x in g), default="")
        tag = f"{iso[8:10]}.{iso[5:7]}." if len(iso) >= 10 else ""
        name = esc(g[0].get("nachname", "") or "")
        name_html = f'<span class="bk-name">{name}</span>' if name else ""
        labels = list(dict.fromkeys(x.get("label", "") for x in g if x.get("label")))
        label_txt = " · ".join(labels)
        if len(g) > 1:
            n = f"{len(g)} Vorgänge"
            label_txt = f"{n} · {label_txt}" if label_txt else n
        rows.append(
            f'<div class="bk-row"><span class="bk-badge {cls}">{badge}</span>'
            f'<span class="bk-val">{_euro(total)}</span>'
            f'{name_html}'
            f'<span class="bk-label">{esc(label_txt)}</span>'
            f'<span class="bk-date">{tag}</span></div>')
    return '<div class="bk-list">' + "".join(rows) + "</div>"


def _activity_list(items):
    # Alles zu EINER Person bündeln: eingehende Mail + gesendete Antwort eines Vorgangs
    # (und mehrere Vorgänge derselben Person) erscheinen in EINER Karte.
    def _name_key(it):
        n = (it.get("kontakt") or "").strip()
        return re.sub(r"[^a-z0-9äöüß]", "", n.lower()) if (n and "@" not in n) else ""

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
        lines = [esc(x.get("text", "")) for x in ins if x.get("text")]
        lines += ['<span class="act-reply">↗️ Antwort:</span> ' + esc(x.get("text", ""))
                  for x in outs if x.get("text")]
        body = "<br>".join(lines)
        rows.append(
            f'<div class="act-row {cls}">'
            f'<div class="act-head">{lead}<span class="act-kontakt">{esc(kontakt)}</span>'
            f'{flag}<span class="act-date">{tag}</span></div>'
            f'<div class="act-betreff">{esc(betreff)}</div>'
            f'<div class="act-text">{body}</div></div>')
    return '<div class="act-list">' + "".join(rows) + "</div>"


def _section(h, needle):
    """Hero-Section anhand eines Titel-Stichworts finden."""
    if not h:
        return None
    return next((s for s in h.hero_sections if needle in s["title"]), None)


_WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ===================== URL-Status (Navigation/Tagewahl/Aktualisieren) =====================
def _url(**changes):
    """Aktuelle Query-Parameter mit Änderungen mischen → relativer Link. None entfernt."""
    p = {k: v for k, v in st.query_params.items()}
    p.pop("do", None)
    for k, v in changes.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    return ("?" + urlencode(p)) if p else "?"


# „Aktualisieren"/„Neu rechnen" sind Links mit ?do=… – Aktion ausführen, Param wegräumen.
_do = st.query_params.get("do")
if _do in ("refresh", "live"):
    load_all.clear()
    st.session_state["_mode"] = "drive" if _do == "refresh" else "live"
    try:
        del st.query_params["do"]
    except KeyError:
        pass
    st.rerun()

nav = st.query_params.get("nav", "heute")
if nav not in ("heute", "vorgaenge", "einnahmen", "reichweite"):
    nav = "heute"

results, _data_ts = load_all(st.session_state.pop("_mode", "auto"))
_stand = datetime.fromtimestamp(_data_ts).strftime("%d.%m. · %H:%M") if _data_ts else "—"
hero = next((r for r in results if r.category == Category.HEUTE), None)

NAV = [("heute", "🎯 Heute"), ("vorgaenge", "📋 Vorgänge"),
       ("einnahmen", "💶 Einnahmen"), ("reichweite", "📣 Reichweite")]
CATEGORY_ICON = {Category.EINNAHMEN: "💶", Category.PIPELINE: "📨"}


# ===================== Bereichsinhalte zusammenbauen =====================
def _heute_html():
    if not (hero and hero.ok and hero.bands):
        if hero:
            return _group_html(hero)
        return '<div class="mm-info">🚢 Noch keine Daten – auf „Neu rechnen" tippen.</div>'
    parts = [_hero_html(hero.bands)]
    bsec = _section(hero, "Buchungen")
    if bsec:
        _want = ["Buchungsprovision heute", "Festbuchungen heute",
                 "Neue Optionen heute", "Festbuchungen Monat"]
        festb = [m for w in _want for m in bsec["metrics"] if m.label == w]
        parts.append(_grid_html(festb, 4))
    gsec = _section(hero, "Accountwachstum")
    if gsec and gsec.get("metrics"):
        parts.append(_growth_html(gsec["metrics"]))
    tsec = _section(hero, "Tageseinnahmen")
    if tsec and tsec.get("metrics"):
        parts.append(_label_html("💶 Tageseinnahmen") + _grid_html(tsec["metrics"], 4))
    return "".join(parts)


def _vorgaenge_html():
    bsec = _section(hero, "Buchungen")
    if not bsec:
        return '<div class="mm-empty">Keine Vorgangsdaten verfügbar.</div>'
    parts = [_grid_html(bsec["metrics"], 5), _label_html("📋 Buchungen & Optionen")]

    bookings = bsec.get("list") or []
    activity = bsec.get("activity") or []
    today_d = datetime.now().date()
    day_opts = [today_d - timedelta(days=i) for i in range(7)]
    active = {it.get("date") for it in bookings} | {a.get("date") for a in activity}
    default_day = next((d for d in day_opts if d.isoformat() in active), today_d)
    sel = st.query_params.get("day") or default_day.isoformat()

    def _day_label(d):
        i = (today_d - d).days
        return ("Heute" if i == 0 else "Gestern" if i == 1
                else f"{_WD[d.weekday()]} {d.strftime('%d.%m.')}")

    pills = "".join(
        f'<a class="mm-pill{" active" if d.isoformat() == sel else ""}" '
        f'href="{_url(nav="vorgaenge", day=d.isoformat())}">{esc(_day_label(d))}</a>'
        for d in day_opts)
    parts.append(f'<div class="mm-pills">{pills}</div>')

    day_items = [it for it in bookings if it.get("date") == sel]
    if day_items:
        parts.append(_booking_list(day_items))
    parts.append(_label_html("🗒️ Postfach-Aktivität"))
    day_act = [a for a in activity if a.get("date") == sel]
    parts.append(_activity_list(day_act) if day_act else '<div class="mm-empty">Keine Aktivität.</div>')
    return "".join(parts)


def _einnahmen_html():
    parts = []
    lsec = _section(hero, "fakturiert")
    if lsec and lsec.get("metrics"):
        parts.append(_label_html(lsec["title"]) + _grid_html(lsec["metrics"], 4))
    for category in (Category.EINNAHMEN, Category.PIPELINE):
        group = [r for r in results if r.category == category]
        if not group:
            continue
        parts.append(_label_html(f"{CATEGORY_ICON[category]} {category.value}"))
        parts.extend(_group_html(res) for res in group)
    return "".join(parts) or '<div class="mm-empty">Keine Einnahmen-Daten verfügbar.</div>'


def _reichweite_html():
    parts = []
    gsec = _section(hero, "Accountwachstum")
    if gsec and gsec.get("metrics"):
        parts.append(_social_html(gsec["metrics"]))

    vanity = [r for r in results if r.category == Category.VANITY]
    kit_r = next((r for r in vanity if "Morrletter" in r.name), None)
    bc_r = next((r for r in vanity if r.name == "Letzte Aussendung"), None)
    yt_r = next((r for r in vanity if r.name.startswith("YouTube")), None)

    if kit_r and kit_r.ok:
        metrics = list(kit_r.metrics) + (list(bc_r.metrics) if (bc_r and bc_r.ok) else [])
        parts.append(_label_html("📧 Morrletter") + _grid_html(metrics, 4))
        if kit_r.caption:
            parts.append(_cap_html(kit_r.caption))
        if bc_r and bc_r.ok and bc_r.caption:
            parts.append(_cap_html("📨 Letzte Aussendung · " + bc_r.caption))
    elif kit_r:
        parts.append(_group_html(kit_r))

    if yt_r and yt_r.ok:
        parts.append(_label_html("▶️ YouTube") + _grid_html(yt_r.metrics, 4))
        if yt_r.caption:
            parts.append(_cap_html(yt_r.caption))
    elif yt_r:
        parts.append(_group_html(yt_r))
    return "".join(parts) or '<div class="mm-empty">Keine Reichweiten-Daten verfügbar.</div>'


_CONTENT = {"heute": _heute_html, "vorgaenge": _vorgaenge_html,
            "einnahmen": _einnahmen_html, "reichweite": _reichweite_html}


# ===================== Seite rendern (EIN HTML-Block) =====================
sidebar = (
    f'<div class="mm-brand">{_LOGO_WHITE}<span>Daily Morr</span></div>'
    + "".join(
        f'<a class="mm-nav{" active" if key == nav else ""}" href="{_url(nav=key, day=None)}">{esc(label)}</a>'
        for key, label in NAV)
    + f'<div class="mm-foot"><b>morr.de</b><br>Stand {esc(_stand)}<br>'
      f'<span class="sail">🚢</span> alle Quellen live</div>'
)

title = dict(NAV).get(nav, "🎯 Heute")
topbar = (
    f'<div class="mm-topbar"><div class="mm-title">{esc(title)}</div>'
    f'<div class="mm-actions">'
    f'<a class="mm-btn" href="{_url(do="refresh")}" title="Neuesten Hintergrund-Stand holen (Sekunden)">🔄 Aktualisieren</a>'
    f'<a class="mm-btn mm-btn-primary" href="{_url(do="live")}" title="Alle Quellen direkt neu rechnen (Minuten)">🐢 Neu rechnen</a>'
    f'</div></div>'
)

content = _CONTENT[nav]()
st.html(
    f'<div class="mm-shell"><aside class="mm-side">{sidebar}</aside>'
    f'<main class="mm-main">{topbar}{content}</main></div>'
)
