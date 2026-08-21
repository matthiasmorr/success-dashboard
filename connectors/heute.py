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

from . import (aida_pvn, digistore, graph, kit, kreuzfahrtstudio, landausfluege, lexware,
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
    # Ø-Tag als Brücke über den Export-Verzug: die Excel ist meist ein paar Tage alt,
    # die Tage danach stünden sonst auf 0 €. Sie bekommen stattdessen den Durchschnitt
    # des letzten Excel-Fensters gutgeschrieben (Details in kreuzfahrtstudio.tagesschnitt).
    ks_schnitt = kreuzfahrtstudio.tagesschnitt(ok_rows, ks_cutoff)
    led = _safe(ledger.summary) if graph.configured() else None
    land = _safe(landausfluege.summary) if graph.configured() else None
    yt = _safe(youtube_revenue.summary) if youtube_revenue.configured() else None
    yt_stats = _safe(youtube.channel_stats)
    postfach = _safe(postfach_summary.summaries) if graph.configured() else None
    lex = _safe(lexware_income.summary) if lexware.configured() else None
    pvn = _safe(aida_pvn.summary) if aida_pvn.configured() else None
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

    def _offene_tage(start, end):
        """Tage im Fenster, die hinter dem Excel-Export-Stand liegen (höchstens bis heute).
        Ist die Excel aktuell (Cutoff = heute), sind es 0 – die Hochrechnung schaltet
        sich dann von selbst ab."""
        if not ks_cutoff:
            return 0
        a, b = max(start, ks_cutoff + timedelta(days=1)), min(end, today)
        return max(0, (b - a).days + 1)

    def _est_prov(start, end):
        """Hochgerechnete Provision für die noch nicht exportierten Tage des Fensters."""
        return _offene_tage(start, end) * ks_schnitt["prov_tag"] if ks_schnitt else 0.0

    def _est_vol(start, end):
        """Hochgerechnetes Buchungsvolumen für die noch nicht exportierten Tage."""
        return _offene_tage(start, end) * ks_schnitt["vol_tag"] if ks_schnitt else 0.0

    # Festbuchungen heute / laufender Monat – HYBRID (Excel autoritativ ≤ Cutoff, Mails danach),
    # damit die Kacheln mit der Excel abgeglichen sind (nicht nur Mail-Recall).
    festwert_heute_h, n_fest_heute_h = _festwert(today, today), _festcount(today, today)
    festwert_monat_h, n_fest_monat_h = _festwert(month_start, today), _festcount(month_start, today)
    festwert_heute_h += _est_vol(today, today)
    festwert_monat_h += _est_vol(month_start, today)

    def _erfolg(fest_prov, land_prov, d_digi, d_awin, d_pvn, yt_val):
        return ((fest_prov or 0) + (land_prov or 0) + (d_digi or 0) + (d_awin or 0)
                + (d_pvn or 0) + (yt_val or 0))

    yt_day = yt["typical_day"] if yt else 0.0   # tagesweise: Median (3 T. Verzug)
    # AIDA PVN = Affiliate wie Awin (AIDA lief bis Mitte 2026 über Awin und zählte
    # dort in den Erfolg). NICHT die Newsletter-Werbung – die ist die „AIDA"-Rechnung
    # in Lexware, ein separater Einnahmestrom.
    fp_h, fp_g = _festprov(today, today), _festprov(gestern, gestern)
    fp_7, fp_30 = _festprov(d7, today), _festprov(d30, today)
    # Hochrechnung für die Tage nach dem Export-Stand – in der Aufstellung eigene Zeile,
    # damit sichtbar bleibt, was aus der Excel kommt und was geschätzt ist.
    est_h, est_g = _est_prov(today, today), _est_prov(gestern, gestern)
    est_7, est_30 = _est_prov(d7, today), _est_prov(d30, today)
    land_h = land["today_prov"] if land else 0.0
    land_g = land["yesterday_prov"] if land else 0.0
    land_7 = land["prov_7d"] if land else 0.0
    land_30 = land["prov_30d"] if land else 0.0
    pvn_h = pvn["today_prov"] if pvn else 0.0
    pvn_g = pvn["yesterday_prov"] if pvn else 0.0
    pvn_7 = pvn["prov_7d"] if pvn else 0.0
    pvn_30 = pvn["prov_30d"] if pvn else 0.0
    yt_7 = yt["rev_7d"] if yt else 0.0   # 7/30 T.: echte YouTube-Umsätze
    yt_30 = yt["rev_30d"] if yt else 0.0

    e_heute = _erfolg(fp_h + est_h, land_h, digi_h, awin_h, pvn_h, yt_day)
    e_gestern = _erfolg(fp_g + est_g, land_g, digi_g, awin_g, pvn_g, yt_day)
    e_7d = _erfolg(fp_7 + est_7, land_7, digi_7, awin_7, pvn_7, yt_7)
    # Lexware-Einnahmen (fakturiert, monatlich/laggy) NUR ins 30-Tage-Band
    e_30d = _erfolg(fp_30 + est_30, land_30, digi_30, awin_30, pvn_30, yt_30) + lex_30d

    def _bd(fest_p, land_p, d_digi, d_awin, d_pvn, yt_v, yt_lbl="YouTube", lex_v=None,
            est_p=0.0):
        """Aufstellung der Bestandteile – klappt in der Hero-Reihe per Klick auf.
        `est_p` (Ø-Hochrechnung für die Tage nach dem Excel-Stand) bekommt eine eigene
        Zeile, damit Excel-Wahrheit und Schätzung nicht verschwimmen."""
        rows = [("🚢 Buchungsprovision", fest_p)]
        if est_p:
            rows.append((_est_lbl, est_p))
        rows += [("🏝️ Landausflüge", land_p),
                 ("🛒 Digistore", d_digi), ("🔗 Awin", d_awin),
                 ("🅰️ AIDA-Affiliate (PVN)", d_pvn), ("▶️ " + yt_lbl, yt_v)]
        if lex_v is not None:
            rows.append(("🧾 Lexware fakturiert", lex_v))
        return [(lbl, _eur(v or 0.0)) for lbl, v in rows]

    _est_lbl = "📊 Ø-Hochrechnung"
    if ks_schnitt and ks_cutoff:
        _est_lbl = (f"📊 Ø-Hochrechnung ab {(ks_cutoff + timedelta(days=1)).strftime('%d.%m.')}")

    wt = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    cap = f"{wt[today.weekday()]}, {today.strftime('%d.%m.%Y')}"
    _satz_now = kreuzfahrtstudio.provision_satz(today) * 100
    _satz_txt = (f"{_satz_now:.1f} %".replace(".", ",")
                 + ("" if today >= kreuzfahrtstudio.PROVISION_AB else " (ab 01.07. 7,5 %)"))
    erfolg_help = (f"Einnahmen = Festbuchungs-Provision ({_satz_txt}) + Landausflüge + Digistore + "
                   "Awin + AIDA-Affiliate (PVN) + YouTube. Im 30-Tage-Band zusätzlich die fakturierten "
                   "Einnahmen aus Lexware (AIDA-Newsletter-Werbung, Sponsoren, Amazon, Meta …). "
                   "Festbuchungen aus der Kreuzfahrtstudio-Excel. Optionen = PIPELINE, nicht enthalten.")
    if ks_schnitt:
        erfolg_help += (
            f" Die Excel reicht bis {ks_schnitt['end'].strftime('%d.%m.')}; für die Tage danach "
            f"zählt der Ø-Tag aus {ks_schnitt['start'].strftime('%d.%m.')}–"
            f"{ks_schnitt['end'].strftime('%d.%m.')} "
            f"({_eur(ks_schnitt['prov_tag'])}/Tag, {ks_schnitt['n']} Buchungen im Fenster) – "
            "Schätzung, die der nächste Export ersetzt.")
    bands = [
        {"label": "🎯 Erfolg heute", "value": _eur(e_heute), "sub": cap,
         "help": erfolg_help, "variant": "",
         "breakdown": _bd(fp_h, land_h, digi_h, awin_h, pvn_h, yt_day, "YouTube (Ø-Tag)",
                          est_p=est_h)},
        {"label": "Erfolg gestern", "value": _eur(e_gestern),
         "sub": f"{wt[gestern.weekday()]}, {gestern.strftime('%d.%m.%Y')}",
         "help": erfolg_help, "variant": "erfolg-band--prev",
         "breakdown": _bd(fp_g, land_g, digi_g, awin_g, pvn_g, yt_day, "YouTube (Ø-Tag)",
                          est_p=est_g)},
        {"label": "Erfolg 7 Tage", "value": _eur(e_7d),
         "sub": f"{d7.strftime('%d.%m.')} – {today.strftime('%d.%m.')}",
         "help": erfolg_help, "variant": "erfolg-band--prev",
         "breakdown": _bd(fp_7, land_7, digi_7, awin_7, pvn_7, yt_7, est_p=est_7)},
        {"label": "Erfolg 30 Tage", "value": _eur(e_30d),
         "sub": f"{d30.strftime('%d.%m.')} – {today.strftime('%d.%m.')}",
         "help": erfolg_help, "variant": "erfolg-band--prev",
         "breakdown": _bd(fp_30, land_30, digi_30, awin_30, pvn_30, yt_30, lex_v=lex_30d,
                          est_p=est_30)},
    ]

    # BEREICH 1 „Buchungen & Optionen" -------------------------------------
    buchungen: list[Metric] = []
    _cut_str = ks_cutoff.strftime("%d.%m.%Y") if ks_cutoff else "—"
    if fest_entries:
        _satz_h = kreuzfahrtstudio.provision_satz(today) * 100
        _satz_str = str(_satz_h).rstrip("0").rstrip(".").replace(".", ",")
        # Liegt der Tag hinter dem Export-Stand, steht in den Kacheln der Ø-Tag – dieselbe
        # Zahl wie im Hero. Der Chip sagt, dass es eine Hochrechnung ist.
        _est_chip = (f"≈ Ø {ks_schnitt['start'].strftime('%d.%m.')}–{ks_schnitt['end'].strftime('%d.%m.')}"
                     if ks_schnitt else "≈ Ø-Tag")
        _est_help = (f" Heute liegt hinter dem Export-Stand ({_cut_str}) – gezeigt wird der "
                     f"Ø-Tag aus {ks_schnitt['start'].strftime('%d.%m.')}–"
                     f"{ks_schnitt['end'].strftime('%d.%m.')}, den der nächste Export ersetzt."
                     if ks_schnitt else "")
        buchungen.append(Metric("Buchungsprovision heute", _eur(fp_h + est_h),
                                delta=(_est_chip if est_h else f"{_satz_str} %"),
                                delta_color="off",
                                help=f"Provision aus den heutigen Festbuchungen "
                                     f"({_satz_str} % auf den Reisepreis)." + (_est_help if est_h else "")))
        buchungen.append(Metric("Festbuchungen heute", _eur(festwert_heute_h),
                                delta=(_est_chip if est_h else
                                       f"{n_fest_heute_h} " + ("Vorgang" if n_fest_heute_h == 1 else "Vorgänge")),
                                delta_color="off",
                                help=f"Feste Buchungen mit Buchungsdatum heute. Quelle: Kreuzfahrtstudio-Excel "
                                     f"(Export-Stand {_cut_str}) – neuere Tage erscheinen nach dem nächsten Excel-Update."
                                     + (_est_help if est_h else "")))
        _monat_est = _est_prov(month_start, today)
        _monat_delta = f"{n_fest_monat_h} " + ("Vorgang" if n_fest_monat_h == 1 else "Vorgänge")
        if _monat_est:
            _monat_delta += f" + {_offene_tage(month_start, today)} T. Ø"
        buchungen.append(Metric("Festbuchungen Monat", _eur(festwert_monat_h),
                                delta=_monat_delta, delta_color="off",
                                help=f"Feste Buchungen im laufenden Monat aus der Kreuzfahrtstudio-Excel "
                                     f"(Export-Stand {_cut_str}). Abgeglichen mit der Excel, nicht aus den Mails."
                                     + (f" Die {_offene_tage(month_start, today)} Tage nach dem Export-Stand "
                                        f"sind mit dem Ø-Tag hochgerechnet." if _monat_est else "")))
    if led is not None:
        nopt = led["n_option_heute"]
        buchungen.append(Metric("Neue Optionen heute", _eur(led["option_value_heute"]),
                                delta=f"{nopt} Option" + ("" if nopt == 1 else "en"), delta_color="off",
                                help="Wert heute neu bestätigter Optionen (Pipeline-Zugang, noch keine Einnahme)."))
        pipe_delta = f"{led['pipeline_count']} offene Optionen"
        if led.get("pipeline_missing"):
            pipe_delta += f" · {led['pipeline_missing']}x Preis offen"
        buchungen.append(Metric("Pipeline offen", _eur(led["pipeline_value"]),
                                delta=pipe_delta, delta_color="off",
                                help="Gesamtwert aller offenen Optionen – potenziell, NICHT als Einnahme "
                                     "gezählt. 'Preis offen' = Betrag in keiner Bestätigung erkennbar; "
                                     "die Summe untertreibt dann."))
    if postfach is not None:
        # Leads: eingehende Kundenanfragen ohne Vorgang (KI-erkannt aus dem Postfach)
        n_lead_heute = sum(1 for a in postfach
                           if a.get("anfrage") and a.get("date") == today.isoformat())
        n_lead_7d = sum(1 for a in postfach if a.get("anfrage"))
        buchungen.append(Metric("Anfragen heute", n_lead_heute,
                                delta=f"{n_lead_7d} in 7 Tagen", delta_color="off",
                                help="Neue Kundenanfragen (Leads) aus dem buchung@-Postfach – "
                                     "KI-erkannt. Noch kein Vorgang, aber potenzielle Buchung; "
                                     "erscheinen in der Vorgangsliste als »Anfrage«."))

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
    if pvn is not None:
        tageseinnahmen.append(Metric(
            "AIDA Affiliate", _eur(pvn["today_prov"]),
            delta=f"Monat: {_eur(pvn['month_prov'])}", delta_color="off",
            help="AIDA-PVN-Provision heute (bestätigt + offen) · Monat = laufender Monat. "
                 "Fließt wie Awin in den Erfolg ein. Eigenes Partnernetzwerk – hat nichts "
                 "mit der AIDA-Newsletter-Rechnung (Lexware) zu tun."))
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
            d = (lex.get("last_date") or {}).get(label, "")
            chip = f"zuletzt {d[8:10]}.{d[5:7]}." if len(d) >= 10 else None
            einnahmen.append(Metric(label, _eur(netto), delta=chip, delta_color="off",
                                    help="Fakturiert (netto), letzte 30 Tage – Lexware. "
                                         f"Jüngster Beleg: {d or '–'}."))
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
    # Fallback 1: gerundete Data-API-Zahl. Fallback 2: letzter bekannter Stand aus
    # der Historie (lieber gestriger Wert mit Datum als ein Strich).
    yt_subs = _safe(youtube_revenue.subscribers_exact)
    yt_exact = yt_subs is not None
    if yt_subs is None and yt_stats:
        yt_subs = yt_stats["subs"]
    yt_delta = social.record_and_delta("youtube", yt_subs) if yt_exact else None
    yt_help = ("Abonnenten – exakt via Analytics API (gewonnen − verloren)." if yt_exact
               else "Abonnenten (gerundet, Data API – Analytics-Token prüfen).")
    if yt_subs is None:
        _val, _delta, _stand = social.last_known("youtube")
        if _val is not None:
            yt_subs, yt_delta = _val, _delta
            yt_help = (f"Live-Abruf fehlgeschlagen – letzter bekannter Stand vom "
                       f"{_stand[8:10]}.{_stand[5:7]}.")
    wachstum.append(
        Metric("YouTube", _num(yt_subs) if yt_subs is not None else "–",
               delta=yt_delta, delta_color="off", help=yt_help))
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
