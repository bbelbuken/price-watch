"""Send WhatsApp messages through CallMeBot (free, personal use).

Each person activates CallMeBot from their own phone and gets their own API key.
Credentials come from environment variables (GitHub Secrets), per watch owner:
    PHONE_<OWNER>             e.g. PHONE_ME=+905551112233
    CALLMEBOT_APIKEY_<OWNER>  e.g. CALLMEBOT_APIKEY_ME=123456
"""

from __future__ import annotations

import os

import requests

API = "https://api.callmebot.com/whatsapp.php"


def credentials(owner: str) -> tuple[str, str] | None:
    key = owner.upper()
    phone = os.environ.get(f"PHONE_{key}", "").strip()
    apikey = os.environ.get(f"CALLMEBOT_APIKEY_{key}", "").strip()
    return (phone, apikey) if phone and apikey else None


def send(owner: str, text: str, dry_run: bool = False) -> bool:
    """Send `text` to the watch owner's WhatsApp. Returns True on success."""
    creds = credentials(owner)
    if dry_run or creds is None:
        reason = "dry run" if dry_run else f"no PHONE_/CALLMEBOT_APIKEY_ secrets for '{owner}'"
        print(f"[not sent: {reason}] to {owner}:\n{text}\n")
        return dry_run
    phone, apikey = creds
    try:
        resp = requests.get(
            API, params={"phone": phone, "text": text, "apikey": apikey}, timeout=60
        )
    except requests.RequestException as exc:
        # Don't print the exception text: it can contain the URL with phone + API key,
        # and Actions logs are public on a public repo.
        print(f"CallMeBot request failed for {owner}: {type(exc).__name__}")
        return False
    if resp.status_code != 200:
        print(f"CallMeBot HTTP {resp.status_code} for {owner}")
        return False
    return True
