"""Datenquellen-Connectoren für das Erfolgs-Dashboard.

Jeder Connector stellt eine `fetch(...) -> ConnectorResult` Funktion bereit.
Neue Quellen einfach hier registrieren – app.py iteriert über ALL_CONNECTORS.
"""
from .base import Category, ConnectorResult, Metric
from . import (youtube, youtube_revenue, kit, kit_broadcast, digistore, awin,
               kreuzfahrtstudio, landausfluege, heute)

# Reihenfolge = Anzeige-Reihenfolge im Dashboard
ALL_CONNECTORS = [
    # Erfolg des Tages (ganz oben)
    heute.fetch,
    # Einnahmen
    kreuzfahrtstudio.fetch,
    landausfluege.fetch,
    youtube_revenue.fetch,
    digistore.fetch,
    awin.fetch,
    # Reichweite
    youtube.fetch,
    kit.fetch,
    kit_broadcast.fetch,
]

__all__ = ["Category", "ConnectorResult", "Metric", "ALL_CONNECTORS"]
