"""Gemeinsame Bausteine für alle Datenquellen-Connectoren."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    HEUTE = "Erfolg"
    EINNAHMEN = "Einnahmen"
    PIPELINE = "Pipeline / Aktivität"
    VANITY = "Reichweite"


@dataclass
class Metric:
    label: str
    value: str | int | float
    delta: str | int | float | None = None   # numerisch -> Streamlit färbt grün/rot
    help: str | None = None
    delta_color: str = "normal"   # "normal" (rot/grün), "off" (neutral grau), "inverse"


@dataclass
class ConnectorResult:
    name: str                         # Anzeigename, z.B. "YouTube"
    category: Category
    configured: bool = True           # False = Key/Config fehlt noch
    ok: bool = True                   # False = Aufruf fehlgeschlagen
    metrics: list[Metric] = field(default_factory=list)
    error: str | None = None
    caption: str | None = None        # kleine Zeile unter den Kacheln, z.B. "Gesamt 21.752"
    # Große Hero-Bänder: je {"label","value","sub","help","variant"} (z.B. Erfolg heute/gestern/7T/30T)
    bands: list = field(default_factory=list)
    # Beschriftete Hero-Bereiche: je {"title": str, "metrics": [Metric], "list": list|None}
    hero_sections: list = field(default_factory=list)

    @classmethod
    def missing_config(cls, name: str, category: Category, hint: str) -> "ConnectorResult":
        return cls(name=name, category=category, configured=False, ok=False, error=hint)

    @classmethod
    def failed(cls, name: str, category: Category, error: str) -> "ConnectorResult":
        return cls(name=name, category=category, configured=True, ok=False, error=error)
