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
    """Escape for Telegram's HTML mode; WhatsApp *bold* -> <b>bold</b>, ~strike~ -> <s>strike</s>."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", escaped)
    return re.sub(r"~([^~\n]+)~", r"<s>\1</s>", escaped)


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


# ---------------------------------------------------------------- Telegram (outgoing extras)

def _telegram_call(method: str, payload: dict) -> dict | None:
    url = f"https://api.telegram.org/bot{_env('TELEGRAM_BOT_TOKEN')}/{method}"
    try:
        data = requests.post(url, json=payload, timeout=30).json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Telegram {method} failed: {type(exc).__name__}")
        return None
    if not data.get("ok"):
        print(f"Telegram {method} error: {data.get('description')}")
        return None
    return data


def telegram_reply(chat_id: str, text: str, buttons=None) -> bool:
    """A message to one chat; `buttons` = rows of (label, callback data) for the Worker's menu."""
    payload = {
        "chat_id": chat_id,
        "text": to_telegram_html(text[: TELEGRAM_LIMIT - 500]),
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]
        }
    return _telegram_call("sendMessage", payload) is not None


def telegram_set_commands(commands: list[tuple[str, str]]) -> bool:
    """Show the commands in Telegram's '/' menu."""
    data = _telegram_call("setMyCommands", {
        "commands": [{"command": c, "description": d} for c, d in commands],
    })
    return data is not None


# ---------------------------------------------------------------- Cloudflare Worker (menu + watch list)
# The Telegram menu runs in the Worker (cloudflare/worker.js) and keeps the watch list in its
# D1 database. GitHub runs read the list from there and do the work that needs Akakçe.

def worker_enabled() -> bool:
    return bool(_env("INBOX_URL") and _env("INBOX_TOKEN"))


def _worker(path: str, body: dict | None = None) -> dict | None:
    url = _env("INBOX_URL").rstrip("/") + path
    try:
        resp = requests.request("POST" if body is not None else "GET", url, json=body, timeout=30,
                                headers={"Authorization": f"Bearer {_env('INBOX_TOKEN')}"})
    except requests.RequestException as exc:
        print(f"worker {path} failed: {type(exc).__name__}")
        return None
    if resp.status_code != 200:
        print(f"worker {path} HTTP {resp.status_code}")
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def watches_fetch() -> list[dict] | None:
    """[{id, owner, pos, body, meta}] – None if the Worker can't be reached."""
    data = _worker("/watches")
    return data.get("watches") if data is not None else None


def watches_import(bodies: list[dict]) -> bool:
    return _worker("/watches/import", {"watches": bodies}) is not None


def watches_patch(patches: list[dict]) -> bool:
    """[{id, field: value, …}] – merged into the stored settings; a None value removes the field."""
    return _worker("/watches/patch", {"watches": patches}) is not None


def watches_delete(ids: list[str]) -> bool:
    return _worker("/watches/delete", {"ids": ids}) is not None


def watches_meta(metas: dict[str, dict]) -> bool:
    return not metas or _worker("/watches/meta", {"metas": metas}) is not None


def jobs_fetch() -> list[dict]:
    data = _worker("/jobs")
    return data.get("jobs", []) if data else []


def jobs_ack(ids: list[int]) -> None:
    if ids:
        _worker("/jobs/ack", {"ids": ids})


NTFY_LIMIT = 1800  # ntfy allows 4096 bytes; Turkish letters/emoji take 2-4 bytes each


NTFY_MAX_ACTIONS = 3  # ntfy shows at most 3 buttons per notification
LINK_PREFIX = "🔗 "


def _link_buttons(chunk: str) -> tuple[str, list[dict]]:
    """Turn up to 3 '🔗 <url>' lines into ntfy 'view' buttons named after their watch.

    A watch block looks like '▸ 9070 xt · 🎯 58.000₺' ... '🔗 https://...'; the button
    label is the part between '▸ ' and ' ·'. Links beyond the 3rd stay as text."""
    actions, blocks = [], []
    for block in chunk.split("\n\n"):
        lines = block.split("\n")
        if len(actions) < NTFY_MAX_ACTIONS and lines[-1].startswith(LINK_PREFIX):
            label = lines[0].removeprefix("▸ ").split(" · ")[0].strip()[:24] or "Akakçe"
            actions.append({"action": "view", "label": label, "url": lines[-1][len(LINK_PREFIX):].strip()})
            lines = lines[:-1]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), actions


def ntfy_payloads(topic: str, text: str) -> list[dict]:
    """First line -> notification title, the rest -> body (plain text, *bold* removed),
    links -> buttons. Long bodies become several notifications '<title> (1/2)', '(2/2)'."""
    plain = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    plain = re.sub(r"~([^~\n]+)~", r"(önce \1)", plain)  # no strikethrough in plain text
    title, _, body = plain.partition("\n")
    chunks = split(body.strip() or title, NTFY_LIMIT)
    # ⬇ / ⚠ without the emoji variation selector, so both spellings match
    priority = 4 if any(mark in title for mark in ("⬇", "🥇", "🆕", "⚠")) else 3
    payloads = []
    for i, chunk in enumerate(chunks, 1):
        message, actions = _link_buttons(chunk)
        payload = {
            "topic": topic,
            "title": title if len(chunks) == 1 else f"{title} ({i}/{len(chunks)})",
            "message": message,
            "priority": priority,
        }
        if actions:
            payload["actions"] = actions
        payloads.append(payload)
    return payloads


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
