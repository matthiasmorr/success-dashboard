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
from urllib.parse import quote, urlencode

import streamlit as st
from dotenv import load_dotenv

from connectors import ALL_CONNECTORS, Category, snapshot, webform
from connectors.digistore import _euro

# Zeitzone fest auf Europe/Berlin – der Cloud-Server läuft sonst in UTC (2h Versatz
# bei „Stand" UND bei der „heute"/Tagesgrenzen-Logik).
os.environ["TZ"] = "Europe/Berlin"
if hasattr(time, "tzset"):
    time.tzset()

load_dotenv()
# Gemeinsame Schlüssel mit morrCRM (03.09.26); die lokale .env hat Vorrang
load_dotenv(Path('~/.config/morr/.env').expanduser())

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
        /* Hero-Kachel mobil auf volle Breite – Euro-Betrag bekommt Luft zum Rand */
        .mm-hero { flex-basis:100%; padding:20px 22px; }
        .mm-hero-row { gap:12px; }
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
    .mm-hero { flex:1.7; min-width:min(280px,100%); color:#fff; border-radius:18px; padding:22px 26px;
        background:linear-gradient(135deg,#1B1B6D 0%,#3636D9 100%); box-shadow:0 8px 26px rgba(27,27,109,.22);
        container-type:inline-size; }
    .mm-hero-eyebrow { font-weight:700; text-transform:uppercase; letter-spacing:.13em; font-size:.74rem; color:#d4d3f4; }
    /* Schrift skaliert mit der Kachelbreite (cqw), damit auch 5-stellige Summen
       („20.531,10 €") nie am Rand kleben; 2.4rem = Fallback ohne Container-Queries. */
    .mm-hero-value { font-family:'Fraunces',serif; font-weight:700; font-size:2.4rem;
        font-size:clamp(1.9rem, 12.5cqw, 3.1rem); line-height:1; margin:6px 0 4px; white-space:nowrap; }
    .mm-hero-sub { color:#d4d3f4; font-size:.82rem; font-weight:700; }
    .mm-cmp { flex:1; min-width:104px; background:#fff; border:1px solid #e7e6f7; border-radius:18px;
        padding:16px 18px; display:flex; flex-direction:column; justify-content:center; }
    .mm-cmp-label { color:#9a9ac0; font-size:.72rem; text-transform:uppercase; letter-spacing:.1em;
        font-weight:700; margin-bottom:5px; }
    .mm-cmp-value { font-family:'Fraunces',serif; font-weight:600; font-size:1.55rem; color:#1B1B6D; white-space:nowrap; }
    .mm-cmp-sub { color:#9a9ac0; font-size:.72rem; margin-top:3px; }

    /* Aufklappbare Aufstellung der Erfolgs-Bestandteile */
    details.mm-hero summary, details.mm-cmp summary { list-style:none; cursor:pointer; }
    details.mm-hero summary::-webkit-details-marker,
    details.mm-cmp summary::-webkit-details-marker { display:none; }
    .mm-bd-hint { margin-left:8px; padding:1px 8px; border:1px solid rgba(255,255,255,.4);
        border-radius:999px; font-size:.68rem; white-space:nowrap; }
    .mm-bd-hint::after { content:" ▾"; }
    details[open] .mm-bd-hint::after { content:" ▴"; }
    .mm-bd { margin-top:12px; padding-top:10px; border-top:1px solid rgba(255,255,255,.28);
        display:flex; flex-direction:column; gap:4px; }
    .mm-bd-row { display:flex; justify-content:space-between; gap:14px; font-size:.84rem; font-weight:700; }
    .mm-bd-row span:first-child { font-weight:600; color:#d4d3f4; }
    .mm-bd-row span:last-child { white-space:nowrap; }
    .mm-bd-note { color:#b9b9e8; font-size:.7rem; margin-top:6px; }
    .mm-bd--light { border-top-color:#edeaf9; }
    .mm-bd--light .mm-bd-row { font-size:.74rem; color:#1B1B6D; }
    .mm-bd--light .mm-bd-row span:first-child { color:#9a9ac0; }
    details.mm-cmp[open] { min-width:min(240px,100%); }

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
    .b-anfrage { background:#fff; color:#5a5a86; border:1px dashed #b9b7e0; }
    .bk-val { font-weight:700; color:#1B1B6D; min-width:100px; }
    .bk-name { font-weight:700; color:#1B1B6D; min-width:118px; }
    .bk-label { color:#5a5a86; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .bk-date { color:#9a9ac0; font-size:.82rem; }

    /* ===================== Postfach-Aktivität ===================== */
    /* Gleicher Karten-Aufbau wie die CRM-Liste unten (Kopf → Kontakt → Inhalt →
       Betreff), inkl. der .ld-flag-Pillen – auf dem Handy ist das der Unterschied
       zwischen „auf einen Blick erfassbar" und einer grauen Textwand. */
    .act-list { margin:4px 0 14px; display:flex; flex-direction:column; gap:9px; }
    .act-row { border:1px solid #e7e6f7; border-left:4px solid #d8d6f0; border-radius:12px;
        padding:12px 15px; background:#fff; }
    .act-row.anfrage { border-left-color:#3636D9; }
    .act-row.act-problem { border-left-color:#d98a1f; background:#fff8ef; }
    .act-row.act-out { border-left-color:#3a8a3a; background:#f5fbf5; }
    .act-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .act-kontakt { font-weight:700; color:#1B1B6D; font-size:.98rem; }
    .act-date { color:#9a9ac0; font-size:.78rem; margin-left:auto; white-space:nowrap; }
    .act-meta { color:#9a9ac0; font-size:.78rem; margin:4px 0 0; display:flex; gap:10px; flex-wrap:wrap; }
    .act-meta a { color:#5a5a86; text-decoration:none; }
    .act-meta a:hover { text-decoration:underline; }
    /* Deckel gegen Ausreißer: KI-Zusammenfassungen sind 3 Sätze, ein Roh-Text-Fallback
       füllt sonst den halben Handy-Bildschirm. */
    .act-text { margin-top:7px; padding:8px 11px; background:#f8f7fd; border-radius:9px;
        color:#3a3a5a; font-size:.84rem; line-height:1.45; }
    .act-reply { margin-top:6px; padding:8px 11px; background:#eff7ef; border-radius:9px;
        color:#336033; font-size:.84rem; line-height:1.45; }
    /* Der Deckel sitzt INNEN: mit Clamp auf der gepolsterten Box lugt sonst die
       angeschnittene nächste Zeile ins Padding. */
    .act-text span, .act-reply span { display:-webkit-box; -webkit-box-orient:vertical;
        overflow:hidden; }
    .act-text span { -webkit-line-clamp:5; }
    .act-reply span { -webkit-line-clamp:3; }
    .act-reply b { color:#2f7a2f; }
    .act-betreff { color:#9a9ac0; font-size:.78rem; margin-top:6px;
        overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

    /* ===================== Sales-Leads (CRM-Liste) ===================== */
    .ld-list { margin:4px 0 14px; display:flex; flex-direction:column; gap:9px; }
    .ld-row { border:1px solid #e7e6f7; border-left:4px solid #d8d6f0; border-radius:12px;
        padding:12px 15px; background:#fff; }
    .ld-row.wartet { border-left-color:#d98a1f; background:#fff8ef; }
    .ld-row.anfrage { border-left-color:#3636D9; }
    .ld-row.gebucht { border-left-color:#3a8a3a; }
    .ld-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .ld-name { font-weight:700; color:#1B1B6D; font-size:.98rem; text-decoration:none; }
    .ld-name:hover { text-decoration:underline; }
    .ld-date { color:#9a9ac0; font-size:.78rem; margin-left:auto; white-space:nowrap; }
    .ld-flag { font-size:.64rem; font-weight:700; border-radius:999px; padding:2px 9px; white-space:nowrap; }
    .f-wartet { color:#8a5a10; background:#fdf0dc; }
    .f-anfrage { color:#fff; background:#3636D9; }
    .f-option { color:#1B1B6D; background:#D6D4F2; }
    .f-buchung { color:#fff; background:#3a8a3a; }
    .f-kit { color:#2f7a2f; background:#e4f3e4; }
    .f-kitoff { color:#b23b3b; background:#fbe7e7; }
    .f-nokit { color:#5a5a86; background:#fff; border:1px dashed #b9b7e0; }
    .ld-meta { color:#9a9ac0; font-size:.78rem; margin:4px 0 0; display:flex; gap:10px; flex-wrap:wrap; }
    .ld-meta a { color:#5a5a86; text-decoration:none; }
    .ld-meta a:hover { text-decoration:underline; }
    .ld-tags { display:flex; gap:5px; flex-wrap:wrap; margin-top:6px; }
    .ld-tag { font-size:.66rem; font-weight:700; color:#3636D9; background:#eceaf6;
        border-radius:6px; padding:2px 7px; }
    .ld-wish { margin-top:7px; padding:8px 11px; background:#f8f7fd; border-radius:9px;
        color:#3a3a5a; font-size:.82rem; line-height:1.45; }
    .ld-wish b { color:#1B1B6D; }
    .ld-quote { color:#5a5a86; font-style:italic; margin-top:5px; display:block; }
    .ld-betreff { color:#9a9ac0; font-size:.78rem; margin-top:6px;
        overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

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
    """Live-Berechnung aller Connectoren (langsam) – inkl. Excel-Refresh aus Drive.

    State-Sync: vorher den Drive-Stand (Ledger/Caches/Historie) in die lokalen
    Dateien mergen – wichtig für die Cloud (startet ohne data/) und gegen das
    Auseinanderlaufen von Mac und Cloud. Danach den vereinten Stand hochladen.
    Fehlgeschlagene Connectoren behalten ihr letztes OK-Ergebnis aus dem Snapshot.
    """
    from connectors import drive
    try:
        drive.sync_state_down()
        drive.refresh_festbuchungen(max_age_hours=12)
    except Exception:  # noqa: BLE001
        pass
    results = snapshot.merge_with_previous([fetch() for fetch in ALL_CONNECTORS])
    try:
        drive.sync_state_up()
    except Exception:  # noqa: BLE001
        pass
    return results


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


def _bd_rows(breakdown):
    return "".join(f'<div class="mm-bd-row"><span>{esc(lbl)}</span><span>{esc(val)}</span></div>'
                   for lbl, val in breakdown)


def _hero_html(bands):
    """Held „Erfolg heute" + 3 Vergleichskarten (gestern · 7 T · 30 T) in einer Reihe.
    Mit Aufstellung der Bestandteile klappt jede Kachel per Klick auf (<details>)."""
    hero = bands[0]
    bd = hero.get("breakdown") or []
    if bd:
        cells = [
            f'<details class="mm-hero" title="{esc(hero.get("help", ""))}"><summary>'
            f'<div class="mm-hero-eyebrow">{esc(hero["label"])}</div>'
            f'<div class="mm-hero-value">{hero["value"]}</div>'
            f'<div class="mm-hero-sub">{esc(hero.get("sub", ""))}'
            f'<span class="mm-bd-hint">Aufstellung</span></div>'
            f'</summary><div class="mm-bd">{_bd_rows(bd)}'
            f'<div class="mm-bd-note">Optionen zählen nicht mit – sie sind Pipeline.</div>'
            f'</div></details>'
        ]
    else:   # alter Snapshot ohne Aufstellung → wie bisher, nicht klickbar
        cells = [
            f'<div class="mm-hero" title="{esc(hero.get("help", ""))}">'
            f'<div class="mm-hero-eyebrow">{esc(hero["label"])}</div>'
            f'<div class="mm-hero-value">{hero["value"]}</div>'
            f'<div class="mm-hero-sub">{esc(hero.get("sub", ""))}</div></div>'
        ]
    for b in bands[1:]:
        label = b["label"].replace("Erfolg ", "")
        val = b["value"].split(",")[0] + " €" if "," in b["value"] else b["value"]
        bdb = b.get("breakdown") or []
        if bdb:
            cells.append(
                f'<details class="mm-cmp" title="{esc(b.get("help", ""))}"><summary>'
                f'<div class="mm-cmp-label">{esc(label)}</div>'
                f'<div class="mm-cmp-value">{val}</div>'
                f'<div class="mm-cmp-sub">{esc(b.get("sub", ""))}</div>'
                f'</summary><div class="mm-bd mm-bd--light">{_bd_rows(bdb)}</div></details>')
        else:
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


def _booking_list(items, max_groups: int = 20):
    # Alles zu EINER Person bündeln: mehrere Vorgänge desselben Nachnamens (am selben
    # Tag) werden zu einer Zeile – Werte summiert, Reisen gebündelt. Eine feste Buchung
    # sticht die Option. Einträge ohne Namen bleiben einzeln stehen.
    grouped: list[list[dict]] = []
    index: dict[str, list[dict]] = {}
    for it in items[:120]:
        # Schlüssel = letztes Wort = Nachname („Wolfgang Wolk" aus einer Anfrage
        # bündelt so mit „Wolk" aus dem Vorgangs-Ledger)
        key = (it.get("nachname") or "").strip().lower().split()[-1:]
        key = key[0] if key else ""
        if key and key in index:
            index[key].append(it)
        else:
            g = [it]
            grouped.append(g)
            if key:
                index[key] = g

    rows = []
    for g in grouped[:max_groups]:
        arts = {x.get("art") for x in g}
        if "buchung" in arts:
            badge, cls = "Buchung", "b-buchung"
        elif "option" in arts:
            badge, cls = "Option", "b-option"
        else:
            badge, cls = "Anfrage", "b-anfrage"
        total = sum(x.get("value", 0) or 0 for x in g)
        # 0 € heißt fast immer: Preis im PDF nicht erkannt – ehrlich beschriften
        # statt eine 0-€-Buchung zu behaupten. Anfragen (Leads) haben nie einen Wert.
        if total:
            val_txt = _euro(total)
        else:
            val_txt = "–" if badge == "Anfrage" else "Preis offen"
        iso = max((x.get("date", "") for x in g), default="")
        tag = f"{iso[8:10]}.{iso[5:7]}." if len(iso) >= 10 else ""
        name = esc(next((x.get("nachname") for x in g if x.get("nachname")), "") or "")
        name_html = f'<span class="bk-name">{name}</span>' if name else ""
        labels = list(dict.fromkeys(x.get("label", "") for x in g if x.get("label")))
        label_txt = " · ".join(labels)
        if len(g) > 1:
            n = f"{len(g)} Vorgänge"
            label_txt = f"{n} · {label_txt}" if label_txt else n
        rows.append(
            f'<div class="bk-row"><span class="bk-badge {cls}">{badge}</span>'
            f'<span class="bk-val">{esc(val_txt)}</span>'
            f'{name_html}'
            f'<span class="bk-label">{esc(label_txt)}</span>'
            f'<span class="bk-date">{tag}</span></div>')
    return '<div class="bk-list">' + "".join(rows) + "</div>"


_ANREDE_W = ("frau", "herr", "familie", "fam", "hr", "fr")


def _name_parts(n):
    """(Vorname, Nachname) normalisiert, Anrede entfernt – leer bei Adressen.

    Umlaute aufgelöst, damit 'Kühne' und 'kuehne' denselben Schlüssel ergeben.
    Vorname nur bei ≥2 Namensteilen: 'Herr Kühne' → ('', 'kuehne').
    Firmen-/Sammelabsender ('Booking | Executive Cruises GER') liefern nichts – sonst
    würden zwei unabhängige Reederei-Mails über deren letztes Wort zusammenfallen.
    """
    if not n or "@" in n or "|" in n:
        return "", ""
    parts = [w for w in n.split() if w.strip(".").lower() not in _ANREDE_W]
    if not parts or len(parts) > 3:
        return "", ""

    def _norm(s):
        s = s.lower()
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            s = s.replace(a, b)
        return re.sub(r"[^a-z0-9]", "", s)

    return (_norm(parts[0]) if len(parts) >= 2 else ""), _norm(parts[-1])


def _activity_list(items):
    # Alles zu EINER Person bündeln: eingehende Mail + gesendete Antwort eines Vorgangs
    # (und mehrere Vorgänge derselben Person) erscheinen in EINER Karte.
    def _keys(g):
        """(Nachname, Vornamen) der Gruppe – Merge-Schlüssel + Kollisionsschutz."""
        nach, vor = "", set()
        for x in g:
            for cand in (x.get("name"), x.get("kontakt")):
                v, n = _name_parts(cand)
                nach = nach or n
                if v:
                    vor.add(v)
        return nach, vor

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
    # Zusammenführung über den NACHNAMEN, nicht über den vollen Anzeigenamen: die
    # Website-Formular-Anfrage nennt den vollen Namen („Werner Kühne"), die Antwort
    # darauf läuft in einem eigenen Thread und kennt nur die Anrede („Herr Kühne") –
    # über conversationId oder exakten Namen finden die beiden nie zusammen.
    # Schutz: unterschiedliche Vornamen zum selben Nachnamen = zwei verschiedene
    # Kunden ('Lisa Seidel' / 'Tobias Seidel') und bleiben getrennt.
    merged: list[list[dict]] = []
    by_name: dict[str, list[tuple[set, list[dict]]]] = {}
    for g in groups:
        nach, vor = _keys(g)
        ziel = None
        for vor_alt, g_alt in by_name.get(nach, []) if nach else []:
            if not vor or not vor_alt or (vor & vor_alt):
                ziel = (vor_alt, g_alt)
                break
        if ziel:
            ziel[1].extend(g)
            ziel[0].update(vor)
        else:
            merged.append(g)
            if nach:
                by_name.setdefault(nach, []).append((vor, g))

    def _name_rank(n):
        """Voller Name mit Vorname (3) > Anrede/Einzelwort (2) > Adresse (1)."""
        if not n:
            return 0
        if "@" in n:
            return 1
        parts = n.split()
        anrede = parts[0].lower().rstrip(".") in ("frau", "herr", "familie", "fam", "hr", "fr")
        return 3 if (len(parts) >= 2 and not anrede) else 2

    rows = []
    for g in merged:
        # Anzeigename: bester verfügbarer Kandidat der Gruppe – „Erika Musterfrau"
        # schlägt „Frau Musterfrau" schlägt „erika@…" (KI-Name vor Absendername).
        cands = [x.get("name") for x in g] + [x.get("kontakt") for x in g]
        kontakt = max((c for c in cands if c), key=_name_rank, default="")
        ins = [x for x in g if x.get("direction") != "out"]
        outs = [x for x in g if x.get("direction") == "out"]
        answered = bool(outs)
        problem = any(x.get("problem") for x in ins) and not answered
        anfrage = any(x.get("anfrage") for x in ins)
        cls = ("act-out" if answered else "act-problem" if problem
               else "anfrage" if anfrage else "")
        # Status als Pillen statt als Emoji-Präfix – dieselben .ld-flag-Farben wie im CRM
        flags = ""
        if problem:
            flags += '<span class="ld-flag f-wartet">⚠️ offen</span>'
        if anfrage:
            flags += '<span class="ld-flag f-anfrage">Reiseanfrage</span>'
        if answered:
            flags += '<span class="ld-flag f-kit">✅ beantwortet</span>'
        iso, hhmm = max(((x.get("date", ""), x.get("time", "")) for x in g), default=("", ""))
        tag = f"{iso[8:10]}.{iso[5:7]}." if len(iso) >= 10 else ""
        if tag and hhmm:
            tag += f" · {hhmm}"   # letzte Aktivität des Tages
        betreff = (ins[0].get("betreff") if ins else g[0].get("betreff", "")) or ""
        # Direkt-Kontakt wie im CRM: Antworten geht vom Handy aus mit einem Tipp
        addr = next((x["addr"] for x in ins + outs if x.get("addr")), "")
        meta = ""
        if addr:
            href = f'mailto:{quote(addr)}' + (
                "?subject=" + quote("Re: " + betreff) if betreff else "")
            meta = f'<div class="act-meta"><a href="{href}">✉️ {esc(addr)}</a></div>'
        text = "<br>".join(esc(x.get("text", "")) for x in ins if x.get("text"))
        antwort = "<br>".join(esc(x.get("text", "")) for x in outs if x.get("text"))
        body = f'<div class="act-text"><span>{text}</span></div>' if text else ""
        if antwort:
            body += ('<div class="act-reply"><span><b>↗️ Antwort:</b> '
                     f'{antwort}</span></div>')
        rows.append(
            f'<div class="act-row {cls}">'
            f'<div class="act-head"><span class="act-kontakt">{esc(kontakt)}</span>'
            f'{flags}<span class="act-date">{tag}</span></div>'
            f'{meta}{body}'
            f'<div class="act-betreff">{esc(betreff)}</div></div>')
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
if nav not in ("heute", "vorgaenge", "leads", "einnahmen", "reichweite"):
    nav = "heute"

results, _data_ts = load_all(st.session_state.pop("_mode", "auto"))
_stand = datetime.fromtimestamp(_data_ts).strftime("%d.%m. · %H:%M") if _data_ts else "—"
hero = next((r for r in results if r.category == Category.HEUTE), None)

NAV = [("heute", "🎯 Heute"), ("vorgaenge", "📋 Vorgänge"), ("leads", "🤝 Leads"),
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
        n = len(tsec["metrics"])
        parts.append(_label_html("💶 Tageseinnahmen") + _grid_html(tsec["metrics"], 5 if n >= 5 else 4))
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

    # „Woche" = alle 7 Tage auf einmal (komplette Kontrolle statt Tages-Häppchen)
    pills = (f'<a class="mm-pill{" active" if sel == "woche" else ""}" '
             f'href="{_url(nav="vorgaenge", day="woche")}">📆 Woche</a>')
    pills += "".join(
        f'<a class="mm-pill{" active" if d.isoformat() == sel else ""}" '
        f'href="{_url(nav="vorgaenge", day=d.isoformat())}">{esc(_day_label(d))}</a>'
        for d in day_opts)
    parts.append(f'<div class="mm-pills">{pills}</div>')

    week_iso = {d.isoformat() for d in day_opts}
    if sel == "woche":
        day_items = [it for it in bookings if it.get("date") in week_iso]
        day_act = [a for a in activity if a.get("date") in week_iso]
    else:
        day_items = [it for it in bookings if it.get("date") == sel]
        day_act = [a for a in activity if a.get("date") == sel]

    # Anfragen (Leads) aus dem Postfach in die Vorgangsliste aufnehmen: gleiche
    # Person (Nachname) wird mit bestehender Option/Buchung gebündelt.
    leads = [{"art": "anfrage", "value": 0, "date": a.get("date", ""),
              "nachname": a.get("name") or a.get("kontakt", ""),
              "label": (a.get("betreff") or a.get("text", ""))[:70]}
             for a in day_act if a.get("anfrage")]
    if day_items or leads:
        parts.append(_booking_list(day_items + leads,
                                   max_groups=60 if sel == "woche" else 20))
    parts.append(_label_html("🗒️ Postfach-Aktivität"))
    parts.append(_activity_list(day_act) if day_act else '<div class="mm-empty">Keine Aktivität.</div>')
    return "".join(parts)


_LEAD_FILTER = {
    "": ("Alle", lambda x: True),
    "wartet": ("⏳ Wartet auf Antwort", lambda x: x["offen"]),
    "anfragen": ("🔥 Reiseanfragen", lambda x: x["anfrage"]),
    "nokit": ("📭 Nicht im Morrletter", lambda x: not x["kit_state"]),
    "vorgang": ("🚢 Mit Vorgang", lambda x: bool(x["vorgang"])),
}


def _lead_wish(f):
    """Eine Zeile „Ziel · Dauer · Kabine …" aus den Formularfeldern der Reiseanfrage.

    Dieselbe Funktion baut die Zeile in der Postfach-Aktivität – die beiden Listen
    sollen dieselbe Anfrage identisch zeigen.
    """
    return esc(webform.wish_line(f))


def _leads_html():
    res = next((r for r in results if r.category == Category.LEADS), None)
    if res is None:
        return '<div class="mm-empty">Leads werden beim nächsten Lauf berechnet.</div>'
    if not res.ok:
        return _group_html(res)
    sec = res.hero_sections[0] if res.hero_sections else {}
    leads = sec.get("leads") or []
    parts = [_grid_html(res.metrics, 5)]

    sel = st.query_params.get("f", "")
    if sel not in _LEAD_FILTER:
        sel = ""
    parts.append('<div class="mm-pills">' + "".join(
        f'<a class="mm-pill{" active" if key == sel else ""}" '
        f'href="{_url(nav="leads", f=key or None)}">{esc(label)}'
        f' <b>{sum(1 for x in leads if test(x))}</b></a>'
        for key, (label, test) in _LEAD_FILTER.items()) + "</div>")

    shown = [x for x in leads if _LEAD_FILTER[sel][1](x)]
    if not shown:
        return "".join(parts) + '<div class="mm-empty">Keine Kontakte in dieser Auswahl.</div>'

    rows = []
    for x in shown[:80]:
        cls = ("wartet" if x["offen"] else
               "gebucht" if x["vorgang"] == "festbuchung" else
               "anfrage" if x["anfrage"] else "")
        flags = []
        if x["offen"]:
            still = x["tage_still"]
            flags.append('<span class="ld-flag f-wartet">⏳ wartet'
                         + (f" · {still} T." if still >= 1 else "") + "</span>")
        if x["anfrage"]:
            flags.append('<span class="ld-flag f-anfrage">Reiseanfrage</span>')
        if x["vorgang"] == "festbuchung":
            flags.append('<span class="ld-flag f-buchung">🚢 gebucht</span>')
        elif x["vorgang"] == "option":
            flags.append('<span class="ld-flag f-option">Option</span>')
        if x["kit_state"] == "active":
            flags.append('<span class="ld-flag f-kit">📧 Morrletter</span>')
        elif x["kit_state"]:
            flags.append(f'<span class="ld-flag f-kitoff">📧 {esc(x["kit_label"])}</span>')
        else:
            flags.append('<span class="ld-flag f-nokit">kein Morrletter</span>')

        d = x["datum"]
        tag = f"{d[8:10]}.{d[5:7]}. · {x['zeit']}" if len(d) >= 10 else ""
        betreff = x.get("betreff") or ""
        href = f'mailto:{quote(x["email"])}' + (
            "?subject=" + quote("Re: " + betreff) if betreff else "")
        meta = [f'<a href="{href}">✉️ {esc(x["email"])}</a>']
        if x.get("telefon"):
            meta.append(f'<a href="tel:{quote(x["telefon"])}">📞 {esc(x["telefon"])}</a>')
        meta.append(f'↓ {x["n_in"]} · ↑ {x["n_out"]}')
        e = x["erstkontakt"]
        meta.append(f'seit {e[8:10]}.{e[5:7]}.')
        if x["kit_since"]:
            k = x["kit_since"]
            meta.append(f'Abo seit {k[8:10]}.{k[5:7]}.{k[2:4]}')

        tags = "".join(f'<span class="ld-tag">{esc(t)}</span>' for t in x["kit_tags"][:8])
        wish = _lead_wish(x.get("form") or {})
        wunsch = (x.get("form") or {}).get("wunsch", "")
        wish_html = ""
        if wish or wunsch:
            wish_html = ('<div class="ld-wish">' + wish
                         + (f'<span class="ld-quote">„{esc(wunsch)}"</span>' if wunsch else "")
                         + "</div>")
        rows.append(
            f'<div class="ld-row {cls}"><div class="ld-head">'
            f'<a class="ld-name" href="{href}">{esc(x["name"])}</a>'
            + "".join(flags) + f'<span class="ld-date">{tag}</span></div>'
            f'<div class="ld-meta">' + " ".join(meta) + "</div>"
            + (f'<div class="ld-tags">{tags}</div>' if tags else "")
            + wish_html
            + (f'<div class="ld-betreff">{esc(betreff)}</div>' if betreff else "")
            + "</div>")
    parts.append('<div class="ld-list">' + "".join(rows) + "</div>")
    if len(shown) > 80:
        parts.append(_cap_html(f"… und {len(shown) - 80} weitere Kontakte."))
    if res.caption:
        parts.append(_cap_html(res.caption))
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


_CONTENT = {"heute": _heute_html, "vorgaenge": _vorgaenge_html, "leads": _leads_html,
            "einnahmen": _einnahmen_html, "reichweite": _reichweite_html}


# ===================== Seite rendern (EIN HTML-Block) =====================
sidebar = (
    f'<div class="mm-brand">{_LOGO_WHITE}<span>Daily Morr</span></div>'
    + "".join(
        f'<a class="mm-nav{" active" if key == nav else ""}" '
        f'href="{_url(nav=key, day=None, f=None)}">{esc(label)}</a>'
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
