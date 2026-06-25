"""Buchungs-Pipeline aus dem buchung@-Postfach (Microsoft Graph).

Funnel in Ordnern:
- "Anfragen"       = eingehende Reise-Anfragen (Leads, Top of Funnel)
- "Reisebuchungen" = buchungsbezogene Vorgänge (Korrespondenz: Optionen, Bestätigungen,
                     Stornos, Threads) – Aktivitäts-/Momentum-Signal, NICHT 1:1 Buchungen
- "IBE Buchungen"  = Online-Buchungen über die Buchungsmaschine

Saubere Buchungs-Euro liefert die Kreuzfahrtstudio-Excel; dieser Connector liefert
den tagesaktuellen Postfach-Puls.
"""
from __future__ import annotations

from datetime import date

from . import graph
from .base import Category, ConnectorResult, Metric

NAME = "Buchungs-Pipeline (buchung@)"
CAT = Category.PIPELINE


def fetch() -> ConnectorResult:
    if not graph.configured():
        return ConnectorResult.missing_config(
            NAME, CAT, "Microsoft-Graph-Zugang fehlt (MS_GRAPH_* in .env)")
    try:
        today = date.today()
        month = today.replace(day=1)
        anfragen_m = graph.count_since("Anfragen", month)
        reise_m = graph.count_since("Reisebuchungen", month)
        anfragen_h = graph.count_since("Anfragen", today)
        reise_h = graph.count_since("Reisebuchungen", today)
        ibe_m = graph.count_since("IBE Buchungen", month)
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))

    metrics = [
        Metric("Neue Anfragen (Monat)", anfragen_m, delta=f"{anfragen_h} heute",
               help="Eingehende Reise-Anfragen / Leads im Ordner 'Anfragen'."),
        Metric("Reisebuchungs-Vorgänge (Monat)", reise_m, delta=f"{reise_h} heute",
               help="Mails im Ordner 'Reisebuchungen' – Aktivität/Korrespondenz, nicht 1:1 Buchungen."),
    ]
    if ibe_m is not None:
        metrics.append(Metric("IBE-Online (Monat)", ibe_m, help="Online-Buchungen über die Buchungsmaschine."))

    return ConnectorResult(
        name=NAME, category=CAT, metrics=metrics,
        caption="Saubere Buchungs-Euro: siehe Kreuzfahrtstudio (Excel). Hier der Postfach-Puls.",
    )
