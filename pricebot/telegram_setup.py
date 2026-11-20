"""Find your Telegram chat id (run once per person, on your own PC).

1. Create the bot: in Telegram talk to @BotFather -> /newbot -> copy the token.
2. Each person opens the new bot in Telegram and presses Start (or sends "merhaba").
3. Run:  .venv\\Scripts\\python -m pricebot.telegram_setup
   Paste the token when asked. It lists everyone who wrote to the bot with their chat id.
   Optionally send a test message.

The token is only used locally for these requests and is not saved anywhere.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")


def clean_token(raw: str) -> str:
    """Pull the token out of whatever was pasted (spaces, \\r from Git Bash, quotes,
    or the whole BotFather message). Never prints the token itself."""
    match = TOKEN_RE.search(raw or "")
    if not match:
        raise SystemExit(
            f"That doesn't look like a bot token ({len(raw.strip())} characters pasted). "
            "It should look like 123456789:AAH... - copy it again from @BotFather."
        )
    return match.group(0)


def call(token: str, method: str, **payload) -> dict:
    url = API.format(token=token, method=method)
    print(f"  {method}...", end=" ", flush=True)
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as exc:
            if attempt < 3:
                print(f"{type(exc).__name__}, retrying", end="... ", flush=True)
                time.sleep(5)
                continue
            raise SystemExit(f"\nCan't reach Telegram ({type(exc).__name__}) after 3 tries. "
                             "Telegram is having trouble with this bot right now; try again later.") from None
        if resp.status_code >= 500 and attempt < 3:
            print(f"HTTP {resp.status_code}, retrying", end="... ", flush=True)
            time.sleep(5)  # temporary Telegram hiccup
            continue
        print(f"HTTP {resp.status_code}", flush=True)
        try:
            data = resp.json()
        except ValueError:
            raise SystemExit(f"Telegram HTTP {resp.status_code} (not JSON)") from None
        if not data.get("ok"):
            hint = " - wrong token? Copy it again from @BotFather." if resp.status_code in (401, 404) else ""
            raise SystemExit(f"Telegram error {resp.status_code}: {data.get('description')}{hint}")
        return data["result"]
    raise SystemExit("Telegram keeps answering with a server error; try again in a few minutes.")


def main() -> int:
    token = clean_token(os.environ.get("TELEGRAM_BOT_TOKEN") or getpass.getpass("Bot token (hidden): "))
    me = call(token, "getMe")
    print(f"Bot: @{me['username']}\n")

    chats = {}
    for update in call(token, "getUpdates"):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat
    if not chats:
        print("Nobody has written to the bot yet. Open it in Telegram, press Start, run again.")
        return 1

    print("Chat id       Name")
    for cid, chat in chats.items():
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        user = f" (@{chat['username']})" if chat.get("username") else ""
        print(f"{cid:<13} {name}{user}")
    print("\nPut yours into GitHub Secrets as TELEGRAM_CHAT_ID_ME (hers: TELEGRAM_CHAT_ID_GF).")

    answer = input("\nSend a test message to these chats? [y/N] ").strip().lower()
    if answer == "y":
        for cid in chats:
            call(token, "sendMessage", chat_id=cid, text="✅ Fiyat botu bağlandı. Bildirimler buraya gelecek.")
        print("Sent.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
