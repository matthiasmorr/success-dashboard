"""📣 Social-Reichweite – Instagram · Facebook · TikTok Followerzahlen.

Drei Plattformen, zwei APIs:

* **Instagram + Facebook** über die **Meta Graph API**. EIN langlebiges Page-Access-
  Token (`META_ACCESS_TOKEN`) deckt beide ab: die Facebook-Seite direkt und – sofern
  ein Instagram-Business-/Creator-Account mit der Seite verknüpft ist – auch Instagram.
  `META_PAGE_ID` optional; ohne Angabe wird die erste Seite aus `/me/accounts` genommen.
* **TikTok** über die **Display API** (`TIKTOK_ACCESS_TOKEN`, Scope `user.info.stats`).

Token-Beschaffung ist eine einmalige manuelle Aktion (siehe README). Fehlt ein Token,
liefert die jeweilige Plattform „–" mit klarer Hinweis-Meldung – nie ein harter Fehler.

Zusätzlich: pro Tag ein Snapshot in `data/social_history.json`, damit wir ein
„+X seit gestern" als Delta zeigen können (Wachstum statt nur Momentaufnahme).
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests

from .base import Metric

_HISTORY = Path(__file__).resolve().parent.parent / "data" / "social_history.json"
META_API = "https://graph.facebook.com"
_META_VERSION = os.getenv("META_API_VERSION", "v21.0").strip() or "v21.0"


# ---------------------------------------------------------------- Meta (IG+FB)
def meta_stats() -> dict:
    """{"facebook": int|None, "instagram": int|None, "ig_user": str|None}.

    Wirft requests.HTTPError bei API-Fehlern (z.B. abgelaufenes Token).
    Felder, die nicht abrufbar sind (kein verknüpfter IG-Account), bleiben None.
    """
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN fehlt")

    page_id = os.getenv("META_PAGE_ID", "").strip()
    base = f"{META_API}/{_META_VERSION}"
    fields = "followers_count,fan_count,name,instagram_business_account{followers_count,username}"

    if page_id:
        r = requests.get(f"{base}/{page_id}",
                         params={"fields": fields, "access_token": token}, timeout=20)
        r.raise_for_status()
        page = r.json()
    else:
        r = requests.get(f"{base}/me/accounts",
                         params={"fields": fields, "access_token": token}, timeout=20)
        r.raise_for_status()
        pages = r.json().get("data", [])
        if not pages:
            raise RuntimeError("Keine Facebook-Seite für dieses Token gefunden")
        page = pages[0]

    fb = page.get("followers_count")
    if fb is None:
        fb = page.get("fan_count")
    ig = page.get("instagram_business_account") or {}
    return {
        "facebook": int(fb) if fb is not None else None,
        "instagram": int(ig["followers_count"]) if ig.get("followers_count") is not None else None,
        "ig_user": ig.get("username"),
    }


# ---------------------------------------------------------------- TikTok
def tiktok_followers() -> int:
    """Followerzahl des TikTok-Accounts. Wirft bei fehlendem Token/API-Fehler."""
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN fehlt")
    r = requests.get(
        "https://open.tiktokapis.com/v2/user/info/",
        params={"fields": "follower_count,display_name"},
        headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    data = r.json().get("data", {}).get("user", {})
    return int(data.get("follower_count", 0))


# ---------------------------------------------------------------- Snapshot/Delta
def _load_history() -> dict:
    if _HISTORY.exists():
        try:
            return json.loads(_HISTORY.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def _record(today: str, counts: dict[str, int]) -> dict[str, int]:
    """Heutige Zahlen speichern, vorherigen (jüngsten älteren) Tagesstand je Plattform liefern."""
    hist = _load_history()
    prev: dict[str, int] = {}
    for older in sorted((d for d in hist if d < today), reverse=True):
        for k, v in hist[older].items():
            if k not in prev and isinstance(v, int):
                prev[k] = v
    day = hist.setdefault(today, {})
    day.update(counts)
    try:
        _HISTORY.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return prev


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def record_and_delta(platform: str, value: int | None, today: date | None = None) -> str | None:
    """Tageswert einer Plattform speichern + „+X seit gestern" liefern.

    Für Quellen, deren Kachel anderswo gebaut wird (z.B. YouTube in heute.py), aber die
    denselben Snapshot-Verlauf nutzen sollen.
    """
    if value is None:
        return None
    iso = (today or date.today()).isoformat()
    prev = _record(iso, {platform: int(value)})
    if platform not in prev:
        return None
    d = int(value) - prev[platform]
    return f"{'+' if d >= 0 else ''}{d} seit gestern" if d else "± 0 seit gestern"


def account_metrics(today: date | None = None) -> list[Metric]:
    """Drei Kacheln (Instagram · Facebook · TikTok) für den Accountwachstum-Block.

    Robust: jede Plattform für sich; fehlt Token oder schlägt der Abruf fehl,
    zeigt die Kachel „–" mit Hinweis im Tooltip. Bei Erfolg zusätzlich „+X seit gestern".
    """
    today = today or date.today()
    iso = today.isoformat()
    counts: dict[str, int] = {}
    ig = fb = tk = None
    ig_err = fb_err = tk_err = None
    ig_user = None

    try:
        m = meta_stats()
        ig, fb, ig_user = m["instagram"], m["facebook"], m["ig_user"]
        if ig is None:
            ig_err = "Kein Instagram-Business-Account mit der Seite verknüpft."
    except Exception as e:  # noqa: BLE001
        ig_err = fb_err = str(e)

    try:
        tk = tiktok_followers()
    except Exception as e:  # noqa: BLE001
        tk_err = str(e)

    if ig is not None:
        counts["instagram"] = ig
    if fb is not None:
        counts["facebook"] = fb
    if tk is not None:
        counts["tiktok"] = tk
    prev = _record(iso, counts) if counts else {}

    def _delta(key: str, val: int | None):
        if val is None or key not in prev:
            return None
        d = val - prev[key]
        return f"{'+' if d >= 0 else ''}{d} seit gestern" if d else "± 0 seit gestern"

    ig_help = (f"Instagram @{ig_user} – Follower." if ig_user
               else (ig_err or "Anbindung einzurichten (Meta Graph API)."))
    return [
        Metric("Instagram", _fmt(ig) if ig is not None else "–",
               delta=_delta("instagram", ig), delta_color="off", help=ig_help),
        Metric("Facebook", _fmt(fb) if fb is not None else "–",
               delta=_delta("facebook", fb), delta_color="off",
               help=fb_err or "Facebook-Seite – Follower."),
        Metric("TikTok", _fmt(tk) if tk is not None else "–",
               delta=_delta("tiktok", tk), delta_color="off",
               help=tk_err or "TikTok – Follower."),
    ]
