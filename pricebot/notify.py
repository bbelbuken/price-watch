"""Deliver a message to a watch owner – via Telegram, ntfy or WhatsApp/CallMeBot.

Credentials come from environment variables (GitHub Secrets), per watch owner.
The first channel that is configured for an owner wins, in this order:

  Telegram:
    TELEGRAM_BOT_TOKEN           one bot for everyone (from @BotFather)
    TELEGRAM_CHAT_ID_<OWNER>     e.g. TELEGRAM_CHAT_ID_ME=123456789
  ntfy (push notifications through the free ntfy app, no account):
    NTFY_TOPIC_<OWNER>           a long random topic name only that person knows
    NTFY_SERVER                  optional, default https://ntfy.sh
  WhatsApp via CallMeBot:
    PHONE_<OWNER>                e.g. PHONE_ME=+905551112233
    CALLMEBOT_APIKEY_<OWNER>     e.g. CALLMEBOT_APIKEY_ME=123456

Messages are written WhatsApp-style (*bold*); Telegram gets them converted to HTML,
ntfy gets plain text with the first line as the notification title.
Never print exception texts here: request URLs contain tokens/API keys and Actions
logs are public on a public repo.
"""

from __future__ import annotations

import html
import os
import re

import requests

CALLMEBOT_API = "https://api.callmebot.com/whatsapp.php"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_LIMIT = 4096


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def channel(owner: str) -> str | None:
    key = owner.upper()
    if _env("TELEGRAM_BOT_TOKEN") and _env(f"TELEGRAM_CHAT_ID_{key}"):
        return "telegram"
    if _env(f"NTFY_TOPIC_{key}"):
        return "ntfy"
    if _env(f"PHONE_{key}") and _env(f"CALLMEBOT_APIKEY_{key}"):
        return "whatsapp"
    return None


def to_telegram_html(text: str) -> str:
    """Escape for Telegram's HTML mode and turn WhatsApp *bold* into <b>bold</b>."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", escaped)


def split(text: str, limit: int) -> list[str]:
    """Split on blank lines (between watches) so each part stays under `limit`."""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(block) > limit:  # a single huge block: hard cut
            parts.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        parts.append(current)
    return parts


def _send_telegram(owner: str, text: str) -> bool:
    url = TELEGRAM_API.format(token=_env("TELEGRAM_BOT_TOKEN"))
    chat_id = _env(f"TELEGRAM_CHAT_ID_{owner.upper()}")
    # Leave room for the HTML tags added by the conversion.
    for part in split(text, TELEGRAM_LIMIT - 500):
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": to_telegram_html(part),
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"Telegram request failed for {owner}: {type(exc).__name__}")
            return False
        if resp.status_code != 200:
            # Telegram's error description is safe to show (no token in it).
            try:
                desc = resp.json().get("description", "")
            except ValueError:
                desc = ""
            print(f"Telegram HTTP {resp.status_code} for {owner}: {desc}")
            return False
    return True


NTFY_LIMIT = 1800  # ntfy allows 4096 bytes; Turkish letters/emoji take 2-4 bytes each


def ntfy_payloads(topic: str, text: str) -> list[dict]:
    """First line -> notification title, the rest -> body (plain text, *bold* removed).
    Long bodies become several notifications titled '<title> (1/2)', '(2/2)'."""
    plain = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    title, _, body = plain.partition("\n")
    chunks = split(body.strip() or title, NTFY_LIMIT)
    priority = 4 if any(mark in title for mark in ("📉", "🆕", "⚠️")) else 3
    return [
        {
            "topic": topic,
            "title": title if len(chunks) == 1 else f"{title} ({i}/{len(chunks)})",
            "message": chunk,
            "priority": priority,
        }
        for i, chunk in enumerate(chunks, 1)
    ]


def _send_ntfy(owner: str, text: str) -> bool:
    server = (_env("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    for payload in ntfy_payloads(_env(f"NTFY_TOPIC_{owner.upper()}"), text):
        try:
            resp = requests.post(server, json=payload, timeout=30)
        except requests.RequestException as exc:
            print(f"ntfy request failed for {owner}: {type(exc).__name__}")
            return False
        if resp.status_code != 200:
            print(f"ntfy HTTP {resp.status_code} for {owner}")
            return False
    return True


def _send_whatsapp(owner: str, text: str) -> bool:
    key = owner.upper()
    params = {"phone": _env(f"PHONE_{key}"), "text": text, "apikey": _env(f"CALLMEBOT_APIKEY_{key}")}
    try:
        resp = requests.get(CALLMEBOT_API, params=params, timeout=60)
    except requests.RequestException as exc:
        print(f"CallMeBot request failed for {owner}: {type(exc).__name__}")
        return False
    if resp.status_code != 200:
        print(f"CallMeBot HTTP {resp.status_code} for {owner}")
        return False
    return True


def send(owner: str, text: str, dry_run: bool = False) -> bool:
    """Send `text` to the watch owner. Returns True on success (or in a dry run)."""
    via = channel(owner)
    if dry_run or via is None:
        reason = "dry run" if dry_run else f"no Telegram/ntfy/WhatsApp secrets for '{owner}'"
        print(f"[not sent: {reason}] to {owner}:\n{text}\n")
        return dry_run
    senders = {"telegram": _send_telegram, "ntfy": _send_ntfy, "whatsapp": _send_whatsapp}
    return senders[via](owner, text)
