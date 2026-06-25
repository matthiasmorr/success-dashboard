"""🎯 Erfolg des Tages – das Zentrum: realisierte Einnahmen, tageszentriert.

Vier große Bänder: Erfolg heute / gestern / 7 Tage / 30 Tage. „Erfolg" = REALISIERTE
Einnahme = nur Festbuchungen (über den Vorgangs-Ledger, jede Reise genau EINMAL,
am Festbuchungs-Datum) + Landausflüge-Provision + Digistore + Awin + YouTube.
Offene Optionen sind PIPELINE (potenziell), keine Einnahme.

Jede Teilabfrage ist gekapselt: fällt eine aus, bleibt der Rest stehen.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import requests

from . import (digistore, graph, kit, kreuzfahrtstudio, landausfluege, lexware,
               lexware_income, ledger, postfach_summary, social, youtube, youtube_revenue)
from .base import Category, ConnectorResult, Metric

NAME = "Erfolg"
CAT = Category.HEUTE


def _digistore_range(start: date, end: date) -> float | None:
    key = os.getenv("DIGISTORE_API_KEY", "").strip()
    if not key:
        return None
    data = digistore._call("listTransactions", key,
                           {"from": start.isoformat(), "to": end.isoformat(), "page_size": 1})
    amounts = data.get("summary", {}).get("amounts", {}) or {}
    bucket = amounts.get("EUR") or next(iter(amounts.values()), {})
    return float(bucket.get("earned_amount", 0) or 0)


def _awin_range(start: date, end: date) -> float | None:
    token = os.getenv("AWIN_API_TOKEN", "").strip()
    pub = os.getenv("AWIN_PUBLISHER_ID", "").strip()
    if not token or not pub:
        return None
    r = requests.get(
        f"https://api.awin.com/publishers/{pub}/transactions/",
        params={"startDate": f"{start.isoformat()}T00:00:00",
                "endDate": f"{end.isoformat()}T23:59:59", "timezone": "Europe/Berlin"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    txns = r.json()
    if isinstance(txns, dict):
        txns = txns.get("data", [])
    return sum(float((t.get("commissionAmount") or {}).get("amount", 0) or 0)
               for t in txns if t.get("commissionStatus") in ("approved", "pending"))


def _kit_today(today: date) -> tuple[int, int] | None:
    key = os.getenv("KIT_API_KEY", "").strip()
    if not key:
        return None
    s = kit._stats(key, today, today)
    return int(s.get("new_subscribers", 0)), int(s.get("net_new_subscribers", 0))


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def fetch() -> ConnectorResult:
    today = date.today()
    gestern = today - timedelta(days=1)
    d7, d30 = today - timedelta(days=6), today - timedelta(days=29)

    ks_df = _safe(kreuzfahrtstudio._load_df)
    ok_rows, ks_cutoff = kreuzfahrtstudio.ok_bookings(ks_df) if ks_df is not None else ([], None)
    # Cutoff = jüngstes Buchungsdatum der Excel (offizieller Kreuzfahrtstudio-Export,
    # bis dahin vollständig). Excel ist Wahrheit ≤ Cutoff, Mails füllen den Rest danach.
    # (Früher auf Monatsende gedeckelt – verworfen, weil die frische Excel den laufenden
    #  Monat deutlich vollständiger abbildet als die Mail-Klassifikation.)
    if ks_cutoff:
        ks_cutoff = min(ks_cutoff, today)
    led = _safe(ledger.summary) if graph.configured() else None
    land = _safe(landausfluege.summary) if graph.configured() else None
    yt = _safe(youtube_revenue.summary) if youtube_revenue.configured() else None
    yt_stats = _safe(youtube.channel_stats)
    postfach = _safe(postfach_summary.summaries) if graph.configured() else None
    lex = _safe(lexware_income.summary) if lexware.configured() else None
    kt = _safe(_kit_today, today)
    _eur = digistore._euro

    # Digistore/Awin je Fenster (today/gestern/7d/30d) + laufender Monat
    month_start = today.replace(day=1)
    digi_h, digi_g = _safe(_digistore_range, today, today), _safe(_digistore_range, gestern, gestern)
    digi_7, digi_30 = _safe(_digistore_range, d7, today), _safe(_digistore_range, d30, today)
    digi_m = _safe(_digistore_range, month_start, today)
    awin_h, awin_g = _safe(_awin_range, today, today), _safe(_awin_range, gestern, gestern)
    awin_7, awin_30 = _safe(_awin_range, d7, today), _safe(_awin_range, d30, today)
    awin_m = _safe(_awin_range, month_start, today)
    lex_30d = lex["total_30d"] if lex else 0.0   # fakturierte Einnahmen (Lexware), nur in Erfolg 30T

    led_dict = led["_led"] if led else None

    # Festbuchungen-€ = AUSSCHLIESSLICH die Excel (offizieller Kreuzfahrtstudio-CRM-Export,
    # vollständig & autoritativ). Mails werden NICHT eingemischt: ihre Vorgangs-/Reise-Nr sind
    # zu uneinheitlich für eine verlässliche Dedup (Excel-Buchungsdatum ≠ Mail-Versanddatum →
    # sonst Doppelzählung). Der Mail-Ledger bleibt Quelle für Optionen/Pipeline/Vorgangsliste.
    fest_entries: list[tuple[date, float]] = list(ok_rows)   # (Buchungsdatum, Preis KD)

    def _festwert(start, end):
        return sum(v for d, v in fest_entries if start <= d <= end)

    def _festcount(start, end):
        return sum(1 for d, v in fest_entries if start <= d <= end and v)

    def _festprov(start, end):
        """Festbuchungs-Provision je Fenster – Satz PRO Buchung nach Buchungsdatum
        (6,5 % bis 30.06., 7,5 % ab 01.07.), damit gemischte Fenster korrekt sind."""
        return sum(v * kreuzfahrtstudio.provision_satz(d)
                   for d, v in fest_entries if start <= d <= end)

    # Festbuchungen heute / laufender Monat – HYBRID (Excel autoritativ ≤ Cutoff, Mails danach),
    # damit die Kacheln mit der Excel abgeglichen sind (nicht nur Mail-Recall).
    festwert_heute_h, n_fest_heute_h = _festwert(today, today), _festcount(today, today)
    festwert_monat_h, n_fest_monat_h = _festwert(month_start, today), _festcount(month_start, today)

    def _erfolg(fest_prov, land_prov, d_digi, d_awin, yt_val):
        return (fest_prov or 0) + (land_prov or 0) + (d_digi or 0) + (d_awin or 0) + (yt_val or 0)

    yt_day = yt["typical_day"] if yt else 0.0   # tagesweise: Median (3 T. Verzug)
    e_heute = _erfolg(_festprov(today, today),
                      land["today_prov"] if land else 0.0, digi_h, awin_h, yt_day)
    e_gestern = _erfolg(_festprov(gestern, gestern),
                        land["yesterday_prov"] if land else 0.0, digi_g, awin_g, yt_day)
    e_7d = _erfolg(_festprov(d7, today), land["prov_7d"] if land else 0.0,
                   digi_7, awin_7, yt["rev_7d"] if yt else 0.0)   # 7/30 T.: echte YouTube-Umsätze
    # Lexware-Einnahmen (fakturiert, monatlich/laggy) NUR ins 30-Tage-Band
    e_30d = _erfolg(_festprov(d30, today), land["prov_30d"] if land else 0.0,
                    digi_30, awin_30, yt["rev_30d"] if yt else 0.0) + lex_30d

    wt = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    cap = f"{wt[today.weekday()]}, {today.strftime('%d.%m.%Y')}"
    _satz_now = kreuzfahrtstudio.provision_satz(today) * 100
    _satz_txt = (f"{_satz_now:.1f} %".replace(".", ",")
                 + ("" if today >= kreuzfahrtstudio.PROVISION_AB else " (ab 01.07. 7,5 %)"))
    erfolg_help = (f"Einnahmen = Festbuchungs-Provision ({_satz_txt}) + Landausflüge + Digistore + "
                   "Awin + YouTube. Im 30-Tage-Band zusätzlich die fakturierten Einnahmen aus Lexware "
                   "(AIDA, Sponsoren, Amazon, Meta …). Festbuchungen aus der Kreuzfahrtstudio-Excel. "
                   "Optionen = PIPELINE, nicht enthalten.")
    bands = [
        {"label": "🎯 Erfolg heute", "value": _eur(e_heute), "sub": cap,
         "help": erfolg_help, "variant": ""},
        {"label": "Erfolg gestern", "value": _eur(e_gestern),
         "sub": f"{wt[gestern.weekday()]}, {gestern.strftime('%d.%m.%Y')}",
         "help": erfolg_help, "variant": "erfolg-band--prev"},
        {"label": "Erfolg 7 Tage", "value": _eur(e_7d),
         "sub": f"{d7.strftime('%d.%m.')} – {today.strftime('%d.%m.')}",
         "help": erfolg_help, "variant": "erfolg-band--prev"},
        {"label": "Erfolg 30 Tage", "value": _eur(e_30d),
         "sub": f"{d30.strftime('%d.%m.')} – {today.strftime('%d.%m.')}",
         "help": erfolg_help, "variant": "erfolg-band--prev"},
    ]

    # BEREICH 1 „Buchungen & Optionen" -------------------------------------
    buchungen: list[Metric] = []
    _cut_str = ks_cutoff.strftime("%d.%m.%Y") if ks_cutoff else "—"
    if fest_entries:
        _satz_h = kreuzfahrtstudio.provision_satz(today) * 100
        buchungen.append(Metric("Buchungsprovision heute", _eur(_festprov(today, today)),
                                delta=f"{str(_satz_h).rstrip('0').rstrip('.').replace('.', ',')} %",
                                delta_color="off",
                                help=f"Provision aus den heutigen Festbuchungen "
                                     f"({str(_satz_h).rstrip('0').rstrip('.').replace('.', ',')} % auf den Reisepreis)."))
        buchungen.append(Metric("Festbuchungen heute", _eur(festwert_heute_h),
                                delta=f"{n_fest_heute_h} " + ("Vorgang" if n_fest_heute_h == 1 else "Vorgänge"),
                                delta_color="off",
                                help=f"Feste Buchungen mit Buchungsdatum heute. Quelle: Kreuzfahrtstudio-Excel "
                                     f"(Export-Stand {_cut_str}) – neuere Tage erscheinen nach dem nächsten Excel-Update."))
        buchungen.append(Metric("Festbuchungen Monat", _eur(festwert_monat_h),
                                delta=f"{n_fest_monat_h} " + ("Vorgang" if n_fest_monat_h == 1 else "Vorgänge"),
                                delta_color="off",
                                help=f"Feste Buchungen im laufenden Monat aus der Kreuzfahrtstudio-Excel "
                                     f"(Export-Stand {_cut_str}). Abgeglichen mit der Excel, nicht aus den Mails."))
    if led is not None:
        nopt = led["n_option_heute"]
        buchungen.append(Metric("Neue Optionen heute", _eur(led["option_value_heute"]),
                                delta=f"{nopt} Option" + ("" if nopt == 1 else "en"), delta_color="off",
                                help="Wert heute neu bestätigter Optionen (Pipeline-Zugang, noch keine Einnahme)."))
        buchungen.append(Metric("Pipeline offen", _eur(led["pipeline_value"]),
                                delta=f"{led['pipeline_count']} offene Optionen", delta_color="off",
                                help="Gesamtwert aller offenen Optionen – potenziell, NICHT als Einnahme gezählt."))

    # BEREICH 2 „Tageseinnahmen" (live, pro Tag): YouTube · Landausflüge · Awin · DigiStore24
    tageseinnahmen: list[Metric] = []
    if yt is not None:
        tageseinnahmen.append(Metric("YouTube/Tag (typisch)", _eur(yt["typical_day"]),
                                      help=f"Median der Werbeeinnahmen der letzten 30 Tage. "
                                           f"Monat bis dato: {_eur(yt['month'])}. ~3 Tage Datenverzug."))
    if land is not None:
        tageseinnahmen.append(Metric(
            "Meine Landausflüge", _eur(land["today_prov"]),
            delta=f"Gestern {_eur(land['yesterday_prov'])} · Vorgestern {_eur(land['vorgestern_prov'])}",
            delta_color="off", help="Provision (10 %) heute, netto nach Stornos. Darunter Vortage."))
    tageseinnahmen.append(Metric("DigiStore24", _eur(digi_h) if digi_h is not None else "–",
                                 delta=(f"Monat: {_eur(digi_m)}" if digi_m is not None else None),
                                 delta_color="off", help="Digistore24 verdient heute · Monat = laufender Monat."))
    tageseinnahmen.append(Metric("Awin", _eur(awin_h) if awin_h is not None else "–",
                                 delta=(f"Monat: {_eur(awin_m)}" if awin_m is not None else None),
                                 delta_color="off", help="Awin-Provision heute · Monat = laufender Monat."))

    # BEREICH 3 „Einnahmen" (fakturiert, Lexware, letzte 30 Tage netto): je Quelle eine Kachel
    einnahmen: list[Metric] = []
    if lex is not None:
        for label, netto in lex["by_source"].items():
            einnahmen.append(Metric(label, _eur(netto), help="Fakturiert (netto), letzte 30 Tage – Lexware."))
        if not einnahmen:
            einnahmen.append(Metric("Fakturiert (30 T.)", _eur(0),
                                    help="Noch keine fakturierten Einnahmen im Fenster."))

    # BEREICH 3 „Accountwachstum": Morrletter · YouTube · Instagram · Facebook · TikTok
    def _num(n):
        return f"{int(n):,}".replace(",", ".") if n is not None else "–"
    wachstum: list[Metric] = []
    if kt is not None:
        new, net = kt
        wachstum.append(Metric("Morrletter", f"+{new}", delta=net,
                               help="Newsletter: neue Abos heute · Delta = netto nach Abmeldungen."))
    # YouTube: exakte Abozahl via Analytics API (Data API rundet auf 3 sig. Stellen).
    # Fallback auf die gerundete Data-API-Zahl, falls das OAuth-Token klemmt.
    yt_subs = _safe(youtube_revenue.subscribers_exact)
    yt_exact = yt_subs is not None
    if yt_subs is None and yt_stats:
        yt_subs = yt_stats["subs"]
    wachstum.append(
        Metric("YouTube", _num(yt_subs) if yt_subs is not None else "–",
               delta=social.record_and_delta("youtube", yt_subs) if yt_exact else None,
               delta_color="off",
               help="Abonnenten – exakt via Analytics API (gewonnen − verloren)." if yt_exact
                    else "Abonnenten (gerundet, Data API – Analytics-Token prüfen)."))
    wachstum += social.account_metrics()

    if not buchungen and not tageseinnahmen:
        return ConnectorResult.missing_config(NAME, CAT, "Noch keine Quellen verbunden")

    lex_title = "🧾 Einnahmen · fakturiert (letzte 30 Tage)"
    if lex:
        lex_title += f" · {_eur(lex['total_30d'])}"
    sections = [
        {"title": "📋 Buchungen & Optionen", "metrics": buchungen,
         "list": led["items"] if led else None, "activity": postfach},
        {"title": "💶 Tageseinnahmen", "metrics": tageseinnahmen, "list": None},
        {"title": lex_title, "metrics": einnahmen, "list": None},
        {"title": "📣 Accountwachstum", "metrics": wachstum, "list": None},
    ]
    sections = [s for s in sections if s["metrics"]]
    return ConnectorResult(name=NAME, category=CAT, caption=cap,
                           bands=bands, hero_sections=sections)
