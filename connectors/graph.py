"""Microsoft Graph app-only Client für das buchung@-Postfach (Phase 2a, Prod).

Liest das Shared Postfach über eine Azure-App-Registrierung (Mail.Read, app-only).
Token + Ordner-IDs werden prozessweit gecacht. Lazy import von msal/requests,
damit Connectoren ohne Graph-Config trotzdem importierbar bleiben.
"""
from __future__ import annotations

import os
import time
from datetime import date

_token = {"value": None, "exp": 0.0}
_folders: dict[str, str] = {}


def _cfg() -> tuple[str, str, str, str]:
    return (
        os.getenv("MS_GRAPH_TENANT_ID", "").strip(),
        os.getenv("MS_GRAPH_CLIENT_ID", "").strip(),
        os.getenv("MS_GRAPH_CLIENT_SECRET", "").strip(),
        os.getenv("MS_GRAPH_MAILBOX", "").strip(),
    )


def configured() -> bool:
    return all(_cfg())


def _access_token() -> str:
    import msal  # noqa: PLC0415

    tenant, client, secret, _ = _cfg()
    now = time.time()
    if _token["value"] and _token["exp"] > now + 60:
        return _token["value"]
    app = msal.ConfidentialClientApplication(
        client, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=secret)
    res = app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    if "access_token" not in res:
        raise RuntimeError(f"Graph-Token: {res.get('error_description', res.get('error'))}"[:200])
    _token["value"] = res["access_token"]
    _token["exp"] = now + res.get("expires_in", 3600)
    return _token["value"]


def _headers() -> dict:
    return {"Authorization": "Bearer " + _access_token(), "ConsistencyLevel": "eventual"}


def _base() -> str:
    return f"https://graph.microsoft.com/v1.0/users/{_cfg()[3]}"


def folder_id(name: str) -> str | None:
    import requests  # noqa: PLC0415

    if not _folders:
        url = f"{_base()}/mailFolders?$top=100&$select=displayName"
        while url:
            d = requests.get(url, headers=_headers(), timeout=30).json()
            for f in d.get("value", []):
                _folders[f["displayName"].strip()] = f["id"]
            url = d.get("@odata.nextLink")
    return _folders.get(name)


def messages(folder_name: str, top: int = 50,
             select: str = "subject,receivedDateTime,hasAttachments") -> list[dict]:
    """Neueste Nachrichten eines Ordners (für Anhang-Auswertung)."""
    import requests  # noqa: PLC0415

    fid = folder_id(folder_name)
    if not fid:
        return []
    r = requests.get(
        f"{_base()}/mailFolders/{fid}/messages",
        params={"$top": str(top), "$orderby": "receivedDateTime desc", "$select": select},
        headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def messages_since(folder_name: str, since: date, select: str, page: int = 200,
                   max_pages: int = 10) -> list[dict]:
    """Alle Nachrichten eines Ordners ab `since` – serverseitig gefiltert, mit Paging.

    `messages()` liefert nur die neuesten `top` Einträge; für ein 30-Tage-Fenster
    reicht das nicht zuverlässig (Gesendete Elemente sind dicht). $filter auf
    receivedDateTime + $orderby ist bei Graph erlaubt, nachgeladen wird über
    @odata.nextLink (gedeckelt, damit ein volles Postfach den Lauf nicht sprengt).
    """
    import requests  # noqa: PLC0415

    fid = folder_id(folder_name)
    if not fid:
        return []
    url = f"{_base()}/mailFolders/{fid}/messages"
    params: dict | None = {
        "$filter": f"receivedDateTime ge {since.isoformat()}T00:00:00Z",
        "$top": str(page), "$orderby": "receivedDateTime desc", "$select": select}
    out: list[dict] = []
    for _ in range(max_pages):
        r = requests.get(url, params=params, headers=_headers(), timeout=40)
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("value", []))
        url, params = d.get("@odata.nextLink"), None
        if not url:
            break
    return out


def message_body(msg_id: str) -> str:
    """Voller Body (HTML oder Text) einer einzelnen Nachricht."""
    import requests  # noqa: PLC0415

    r = requests.get(f"{_base()}/messages/{msg_id}?$select=body",
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    return ((r.json().get("body") or {}).get("content", "")) or ""


def attachments(msg_id: str) -> list[dict]:
    import requests  # noqa: PLC0415

    r = requests.get(f"{_base()}/messages/{msg_id}/attachments?$select=id,name,contentType",
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def attachment_bytes(msg_id: str, att_id: str) -> bytes:
    import requests  # noqa: PLC0415

    r = requests.get(f"{_base()}/messages/{msg_id}/attachments/{att_id}/$value",
                     headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.content


def messages_from_sender(mailbox: str, sender: str, since: date | None = None, top: int = 200,
                         select: str = "subject,receivedDateTime,from,body") -> list[dict]:
    """Mails einer beliebigen Mailbox, serverseitig nach Absender (+ optional Datum) gefiltert.

    Kein $orderby (Graph verbietet die Kombination $filter(from)+$orderby) – die
    Feinsortierung/-filterung macht der Aufrufer in Python über receivedDateTime.
    `since` grenzt serverseitig ein und hält das Ergebnis unabhängig von der Historie klein.
    Setzt voraus, dass die App-Registrierung Mail.Read auch für `mailbox` hat.
    """
    import requests  # noqa: PLC0415

    filt = f"from/emailAddress/address eq '{sender}'"
    if since:
        filt += f" and receivedDateTime ge {since.isoformat()}T00:00:00Z"
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages",
        params={"$filter": filt, "$top": str(top), "$select": select},
        headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


