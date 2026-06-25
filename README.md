# Erfolgs-Dashboard · morr.de

Strategisches Wachstums-Dashboard (wöchentlich/monatlich) für morr.de.
Streamlit-App, läuft lokal auf dem Mac und später in der Streamlit Community Cloud (Handy-Zugriff).

> Bewusst getrennt vom täglichen Morgen-Report (eigene Aufgabe) und vom Buchungs-CRM.

## Starten

```bash
./run.sh
# oder:
./venv/bin/streamlit run app.py
```

→ öffnet http://localhost:8501

## Konfiguration

Keys in `.env` (Vorlage: `.env.example`). Bereits gesetzt: `YOUTUBE_API_KEY`,
`YOUTUBE_CHANNEL_ID`, `ANTHROPIC_API_KEY` (aus dem Video-Projekt übernommen).

| Variable | Status | Wofür |
|---|---|---|
| `YOUTUBE_API_KEY` / `YOUTUBE_CHANNEL_ID` | ✅ gesetzt | Abos, Aufrufe, Videos |
| `KIT_API_KEY` | ✅ gesetzt | Morrletter-Wachstum + letzte Aussendung |
| `DIGISTORE_API_KEY` | ✅ gesetzt | Digistore24-Umsatz |
| `AWIN_API_TOKEN` / `AWIN_PUBLISHER_ID` | ✅ gesetzt | Awin-Provisionen |
| `ANTHROPIC_API_KEY` | ✅ gesetzt | KI-Auswertung (erst Phase 2) |

## Autonom-Betrieb – Credentials-Checkliste

Phase 1 läuft komplett autonom (Live-APIs). Diese Quellen laufen aktuell als
**Schnappschuss / Dev-Fixture** und werden erst mit eigenem Zugang dauerhaft selbstständig:

| Quelle | Aktuell | Für Autonomie nötig |
|---|---|---|
| Festbuchungen | lokale `.xlsx` (Snapshot) | Google Service Account; Ordner „Kreuzfahrtstudio x MM" freigeben |
| Buchungs-Pipeline (Anfragen/Reisebuchungen) | ✅ **live über Graph** | erledigt – App „Success-Dashboard Mail", `Mail.Read` app-only |
| YouTube-Werbung + tägliche Abos | – | YouTube Analytics OAuth (Phase 2c) |

> 🔒 **Noch offen (Härtung):** Die Graph-App hat `Mail.Read` aktuell **mandantenweit**. Per Exchange-Online **Application Access Policy** auf nur `buchung@morr.de` einschränken. Secret läuft in ~24 Monaten ab (erneuern).

## Architektur

```
app.py                Streamlit-UI (morr.de-Branding, gruppiert nach Einnahmen / Pipeline / Reichweite)
.streamlit/config.toml  Theme (Indigo/Magnolia, Fraunces/Lato)
connectors/
  base.py             ConnectorResult + Metric (gemeinsames Datenmodell)
  kreuzfahrtstudio.py Festbuchungen (.xlsx, pandas)  ✅ live (lokale .xlsx; PROD=Service Account)
  youtube.py          YouTube Data API v3      ✅ live getestet (nur Gesamtstand – tägl. Zugänge = Phase 2c)
  kit.py              KIT v4 growth_stats      ✅ live verifiziert: neu heute + 30 Tage (Key fehlt)
  kit_broadcast.py    KIT v4 broadcast-stats   ✅ live verifiziert: Öffnungs-/Klickrate letzte Aussendung (Key fehlt)
  digistore.py        Digistore24 Vendor-API   ✅ live (listTransactions → summary)
  awin.py             Awin Publisher-API       ✅ live (transactions, approved+pending)
  graph.py            Microsoft-Graph-Client (buchung@, app-only, Mail.Read)
  buchungen.py        Buchungs-Pipeline (Pipeline)  ✅ live (Anfragen/Reisebuchungen/IBE-Zähler)
  booking_value.py    Buchungswert-Tendenz (KI)     ✅ live – liest Gesamtreisepreis aus Bestätigungs-PDFs
                      (Claude Haiku), Dedup je Vorgang, Cache (data/buchungswert_cache.json)
  heute.py            🎯 Erfolg                     ✅ Provision YTD + Buchungswert heute/7T + Tagespuls
  social.py           Instagram·Facebook·TikTok     ✅ IG+FB live (Meta Page-Token, läuft nicht ab) · TikTok wartet auf Token
```

### Social-Reichweite einrichten (`social.py`)

Instagram + Facebook laufen über **eine** Meta Graph API (ein Page-Token deckt beide ab),
TikTok separat. Code degradiert auf „–", solange kein Token gesetzt ist.

1. **Meta-Token (IG + FB):** [developers.facebook.com](https://developers.facebook.com) → App
   (Typ *Business*) → *Graph API Explorer* → Page-Token mit Scopes
   `pages_read_engagement, pages_show_list, instagram_basic, business_management` →
   im *Access Token Tool* zu einem **langlebigen** Token tauschen → in `.env` als
   `META_ACCESS_TOKEN`. Voraussetzung: IG-Account ist *Business/Creator* und mit der
   FB-Seite verknüpft. (Optional `META_PAGE_ID`, sonst erste Seite aus `/me/accounts`.)
2. **TikTok-Token:** [developers.tiktok.com](https://developers.tiktok.com) → App mit
   Scope `user.info.stats` → OAuth-Access-Token → `.env` als `TIKTOK_ACCESS_TOKEN`.

Tages-Snapshots landen in `data/social_history.json` → Kachel zeigt „+X seit gestern".

Design-Tokens stammen aus der morr.de-Astro-Site (`~/website/morr-astro-starter`):
Indigo `#1B1B6D`, Persian `#3636D9`, Magnolia `#F1F0FA`; Schriften Fraunces/Lato.

Neue Quelle = neues Modul mit `fetch() -> ConnectorResult`, in `connectors/__init__.py`
zu `ALL_CONNECTORS` hinzufügen.

## Roadmap (aus TickTick)

- **Phase 1** (jetzt): Digistore + Awin + YouTube + KIT per API, lokal.
- **Phase 2a**: KI-Auswertung Buchungs-Mails (Optionen + Landausflüge), inkrementell.
- **Phase 2b**: KI-Auswertung Excel-Festbuchungen (Google Drive, nur bei Änderung).
- **Phase 3**: Deploy Streamlit Community Cloud (Cache persistent machen!).
- **Phase 2c** (neu): YouTube Analytics API (OAuth) → **tägliche Abo-Zugänge + Werbeeinnahmen** (AdSense doch möglich, anders als ursprünglich angenommen).
- **Phase 4**: Social Media (Facebook + Instagram zusammen über eine Meta-App, TikTok separat).

## Backlog / offene Ideen

- **easybell-Anrufzahl** (≈ Buchungs-Pipeline): öffentliche CDR-API unbestätigt; Cloud-PBX hat evtl. REST-Stats, sonst EVN/Anrufliste-CSV-Export → Datei-Pipeline. Im easybell-Portal zu verifizieren.
- **Mail-Gesundheit buchung@morr.de**: KI-Analyse auf Auffälligkeiten (unbeantwortet, Beschwerden) – an Phase 2a andocken.
- **Datei-basierte Einnahmequellen (keine Live-API)** → ein generischer „Berichte-Ordner"-Connector (CSV/Excel-Report ablegen → pandas/KI liest ein, erkennt Quelle). Deckt mit *einem* Baustein ab:
  - **Amazon KDP** (Tantiemen) – Report-Export
  - **Amazon Associates/PartnerNet** (Affiliate-Provisionen) – keine API, nur CSV-Report
  - **Spreadshirt/Morrchandising** – API unklar (Shop-Order-API vs. Marktplatz-Export); zu verifizieren
  - technisch dasselbe wie die Kreuzfahrtstudio-Excel (Phase 2b)
- **Strategisch vs. operativ:** Tiefe Mail-Problem-Erkennung und Anruf-Details sind eher operativ (→ Daily-Report/CRM). Hier nur die verdichteten Trend-/Gesundheits-Kennzahlen.
