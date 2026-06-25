"""Kreuzfahrtstudio Festbuchungen – Buchungsvolumen, Anzahl, geschätzte Provision.

Quelle: "Geteilte Liste Export Vorgänge Matthias Morr.xlsx" (echte .xlsx)
im Drive-Ordner "Kreuzfahrtstudio x MM".

KEINE KI nötig – die Tabelle ist sauber strukturiert, pandas reicht.
Spalten werden über Namen gemappt (nicht Position), robust gegen Umsortieren.

Datenzugang:
- DEV/lokal:  KREUZFAHRTSTUDIO_XLSX = Pfad zu einer lokal heruntergeladenen .xlsx
- PROD:       KREUZFAHRTSTUDIO_FILE_ID + Google-Service-Account (Drive API) – lazy import

Kennzahlen (laufender Monat, nach Buchungsdatum):
- Buchungsvolumen = Summe "Preis KD" der OK-Buchungen
- Geschätzte Provision = Volumen × Provisionssatz (Default 6,5 %)
- Anzahl Buchungen (OK) + Stornos (nach Stornodatum)
Es werden NUR Aggregate angezeigt, keine Kundendaten.
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime

import pandas as pd

from .base import Category, ConnectorResult, Metric

NAME = "Kreuzfahrtstudio (Festbuchungen)"
CAT = Category.EINNAHMEN
# Provisionssatz ist datumsabhängig: bis 30.06.2026 = 6,5 %, ab 01.07.2026 = 7,5 %.
# Maßgeblich ist das BUCHUNGSDATUM (nicht das Anzeigedatum) – wichtig für gemischte
# Fenster über den Monatswechsel (z.B. 30-Tage-Band Ende Juni/Anfang Juli).
PROVISION_SATZ = float(os.getenv("KREUZFAHRTSTUDIO_PROVISION", "0.065"))        # bis 30.06.2026
PROVISION_SATZ_NEU = float(os.getenv("KREUZFAHRTSTUDIO_PROVISION_NEU", "0.075"))  # ab 01.07.2026
PROVISION_AB = date.fromisoformat(os.getenv("KREUZFAHRTSTUDIO_PROVISION_AB", "2026-07-01"))


def provision_satz(d: date) -> float:
    """Geltender Provisionssatz für eine Buchung mit Buchungsdatum `d`."""
    return PROVISION_SATZ_NEU if d >= PROVISION_AB else PROVISION_SATZ


def _euro(v: float) -> str:
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _find_col(df: pd.DataFrame, *needles: str) -> str | None:
    for col in df.columns:
        low = str(col).strip().lower()
        if any(n in low for n in needles):
            return col
    return None


def _to_date(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s in ("0", "nan", "NaT"):
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_money(v) -> float:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    s = s.replace("€", "").strip().replace(".", "").replace(",", ".")  # 2.856,00 -> 2856.00
    try:
        return float(s)
    except ValueError:
        return 0.0


def _read_excel_smart(source) -> pd.DataFrame:
    """Liest die .xlsx und findet die Header-Zeile dynamisch (Datei hat eine Titelzeile oben)."""
    raw = pd.read_excel(source, header=None, dtype=object)
    hdr = 0
    for i in range(min(8, len(raw))):
        vals = [str(x) for x in raw.iloc[i].tolist()]
        if any("Vorgangsstatus" in v or "Reise-Nr" in v for v in vals):
            hdr = i
            break
    if hasattr(source, "seek"):
        source.seek(0)  # BytesIO für erneutes Lesen zurückspulen
    df = pd.read_excel(source, header=hdr, dtype=object)
    return df.dropna(how="all")


def _load_df() -> pd.DataFrame | None:
    path = os.getenv("KREUZFAHRTSTUDIO_XLSX", "").strip()
    if path and os.path.exists(path):
        return _read_excel_smart(path)

    file_id = os.getenv("KREUZFAHRTSTUDIO_FILE_ID", "").strip()
    if file_id:
        # PROD: Drive API mit Service Account (lazy import, damit DEV ohne google-libs läuft)
        from google.oauth2 import service_account  # noqa: PLC0415
        from googleapiclient.discovery import build  # noqa: PLC0415
        from googleapiclient.http import MediaIoBaseDownload  # noqa: PLC0415

        creds = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        svc = build("drive", "v3", credentials=creds)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return _read_excel_smart(buf)

    return None


_MONATE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"]


def compute(df: pd.DataFrame, today: date | None = None) -> ConnectorResult:
    """Aggregation – getrennt von _load_df, damit gegen Fixtures testbar.

    Zeitraum: 'letzter Monat mit Daten' + 'Jahr bis dato'. Bewusst NICHT der
    laufende Kalendermonat, weil der Kreuzfahrtstudio-Export nur periodisch
    (etwa monatlich) aktualisiert wird und der aktuelle Monat fast immer leer wäre.
    """
    today = today or date.today()
    status_col = _find_col(df, "vorgangsstatus")
    buchung_col = _find_col(df, "buchung")
    price_col = _find_col(df, "preis kd") or _find_col(df, "preis va")

    if not (status_col and buchung_col and price_col):
        return ConnectorResult.failed(
            NAME, CAT,
            f"Spalten nicht gefunden (Status={status_col}, Buchung={buchung_col}, Preis={price_col})",
        )

    def is_ok(row) -> bool:
        return str(row.get(status_col, "")).strip().upper().startswith("OK")

    # OK-Buchungen pro Monat aggregieren
    month_vol: dict[tuple[int, int], float] = {}
    month_cnt: dict[tuple[int, int], int] = {}
    latest: date | None = None
    for r in df.to_dict("records"):
        b = _to_date(r.get(buchung_col))
        if not b:
            continue
        if latest is None or b > latest:
            latest = b
        if is_ok(r):
            ym = (b.year, b.month)
            month_vol[ym] = month_vol.get(ym, 0.0) + _to_money(r.get(price_col))
            month_cnt[ym] = month_cnt.get(ym, 0) + 1

    if not month_vol:
        return ConnectorResult.failed(NAME, CAT, "Keine OK-Buchungen in der Datei gefunden")

    last_ym = max(month_vol)                       # jüngster Monat mit Buchungen
    last_vol, last_cnt = month_vol[last_ym], month_cnt[last_ym]
    ytd_vol = sum(v for (y, _), v in month_vol.items() if y == today.year)
    ytd_cnt = sum(c for (y, _), c in month_cnt.items() if y == today.year)
    monat_label = f"{_MONATE[last_ym[1]]} {str(last_ym[0])[2:]}"   # z.B. "Mai 26"
    rate = provision_satz(date(last_ym[0], last_ym[1], 1))         # Satz des jüngsten Monats
    rate_str = f"{rate*100:.1f}".replace(".", ",")

    caption = (f"{last_cnt} Buchungen im {_MONATE[last_ym[1]]} · "
               f"{ytd_cnt} Buchungen {today.year} bis dato")
    if latest:
        caption += f" · Export-Stand: jüngste Buchung {latest.strftime('%d.%m.%Y')}"

    return ConnectorResult(
        name=NAME,
        category=CAT,
        metrics=[
            Metric(f"Volumen {monat_label}", _euro(last_vol),
                   help=f"Summe 'Preis KD' der {last_cnt} OK-Buchungen im {_MONATE[last_ym[1]]} {last_ym[0]}."),
            Metric(f"Provision ~{rate_str} %", _euro(last_vol * rate),
                   help=f"Geschätzt: Volumen {monat_label} × {rate_str} % "
                        f"(ab 01.07.2026 gilt 7,5 %)."),
            Metric(f"Volumen {today.year} (YTD)", _euro(ytd_vol),
                   help=f"Kumuliertes Buchungsvolumen {today.year}, {ytd_cnt} Buchungen."),
        ],
        caption=caption,
    )


def ok_bookings(df: pd.DataFrame | None = None) -> tuple[list[tuple[date, float]], date | None]:
    """[(Buchungsdatum, Preis KD)] aller OK-Buchungen + jüngstes Buchungsdatum (= Export-Cutoff).

    Für den Hybrid-Festwert: die Excel ist bis zu diesem Cutoff die Wahrheit.
    """
    if df is None:
        df = _load_df()
    if df is None:
        return [], None
    status_col = _find_col(df, "vorgangsstatus")
    buchung_col = _find_col(df, "buchung")
    price_col = _find_col(df, "preis kd") or _find_col(df, "preis va")
    if not (status_col and buchung_col and price_col):
        return [], None
    rows: list[tuple[date, float]] = []
    latest: date | None = None
    for r in df.to_dict("records"):
        b = _to_date(r.get(buchung_col))
        if not b:
            continue
        if latest is None or b > latest:
            latest = b
        if str(r.get(status_col, "")).strip().upper().startswith("OK"):
            rows.append((b, _to_money(r.get(price_col))))
    return rows, latest


def ok_booking_ids(df: pd.DataFrame | None = None) -> set[str]:
    """Alle Identifikatoren der OK-Buchungen (Vorgangs-Nr UND Reise-Nr, je komma-getrennt).

    Für die Deduplizierung Excel ↔ Mail-Ledger: die KI-Klassifikation der Mails greift mal
    die Vorgangs-Nr, mal die Reise-Nr ab – beide müssen als Schlüssel gelten, sonst wird
    dieselbe Buchung doppelt gezählt (Excel-Buchungsdatum ≠ Mail-Versanddatum).
    """
    if df is None:
        df = _load_df()
    if df is None:
        return set()
    status_col = _find_col(df, "vorgangsstatus")
    vg_col = _find_col(df, "vorgangs-nr")
    reise_col = _find_col(df, "reise-nr")
    ids: set[str] = set()
    for r in df.to_dict("records"):
        if not str(r.get(status_col, "")).strip().upper().startswith("OK"):
            continue
        for col in (vg_col, reise_col):
            if not col:
                continue
            for tok in str(r.get(col, "")).replace(";", ",").split(","):
                tok = tok.strip()
                if tok and tok.lower() not in ("nan", "none"):
                    ids.add(tok)
    return ids


def figures(today: date | None = None) -> dict | None:
    """Rohzahlen für andere Connectoren (z.B. den Erfolgs-Hero). None wenn keine Quelle."""
    df = _load_df()
    if df is None:
        return None
    today = today or date.today()
    status_col = _find_col(df, "vorgangsstatus")
    buchung_col = _find_col(df, "buchung")
    price_col = _find_col(df, "preis kd") or _find_col(df, "preis va")
    if not (status_col and buchung_col and price_col):
        return None
    month_vol: dict[tuple[int, int], float] = {}
    latest: date | None = None
    for r in df.to_dict("records"):
        b = _to_date(r.get(buchung_col))
        if not b:
            continue
        if latest is None or b > latest:
            latest = b
        if str(r.get(status_col, "")).strip().upper().startswith("OK"):
            ym = (b.year, b.month)
            month_vol[ym] = month_vol.get(ym, 0.0) + _to_money(r.get(price_col))
    if not month_vol:
        return None
    last_ym = max(month_vol)
    return {
        "rate": PROVISION_SATZ,
        "last_ym": last_ym,
        "last_month_label": f"{_MONATE[last_ym[1]]} {last_ym[0]}",
        "last_vol": month_vol[last_ym],
        "ytd_vol": sum(v for (y, _), v in month_vol.items() if y == today.year),
        "latest_booking": latest,
    }


def fetch() -> ConnectorResult:
    try:
        df = _load_df()
        if df is None:
            return ConnectorResult.missing_config(
                NAME, CAT,
                "Datenquelle fehlt: KREUZFAHRTSTUDIO_XLSX (lokal) oder KREUZFAHRTSTUDIO_FILE_ID + Service Account",
            )
        return compute(df)
    except Exception as e:  # noqa: BLE001
        return ConnectorResult.failed(NAME, CAT, str(e))
