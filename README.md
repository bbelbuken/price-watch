# price-watch

Price bot: every 10 minutes it searches [Akakçe](https://www.akakce.com) for each watch in
[watches.yaml](watches.yaml) and sends one message per person when any of the top 5 products
gets ≥ 1% cheaper, plus a summary at 09:00 and 21:00 (Turkey time). Messages go out as
**ntfy** push notifications for now; Telegram and WhatsApp (CallMeBot) are supported too
(Telegram's Bot API had an outage and CallMeBot wasn't taking new users, Nov 2026).
Runs free on GitHub Actions. Plan: [price-bot-plan.md](price-bot-plan.md).

## Setup (ntfy – current)
1. **Install the ntfy app** (Android: Play Store / F-Droid, iPhone: App Store – "ntfy").
2. **Make up a secret topic name** – anyone who knows it can read your notifications, so make
   it long and random. Generate one:
   ```powershell
   .venv\Scripts\python -c "import secrets; print('pw-' + secrets.token_urlsafe(12))"
   ```
3. In the app: **+** → *Subscribe to topic* → paste the topic name (server: ntfy.sh).
4. **GitHub Secret** (repo → Settings → Secrets and variables → Actions): `NTFY_TOPIC_ME` = the topic.
5. Actions → **price-watch** → **Run workflow**. The first run sends a summary with the top 5
   of every watch; after that only drops and the 09:00 / 21:00 summaries.

Her own notifications: she makes her own topic, subscribes on her phone, and you add
`NTFY_TOPIC_GF` (+ map it in the workflow `env:`).

## Setup (Telegram – once its Bot API works again)
1. **Create the bot** (once): in Telegram open **@BotFather** → `/newbot` → pick a name and a
   username ending in `bot` → copy the **token** it gives you. Keep it secret.
2. **Start the bot**: open your new bot in Telegram (BotFather sends a `t.me/...` link) and press **Start**.
3. **Find your chat id** on your PC:
   ```
   .venv\Scripts\python -m pricebot.telegram_setup
   ```
   Paste the token when asked (hidden, not saved). It prints your chat id and can send a test message.
4. **GitHub Secrets** (repo → Settings → Secrets and variables → Actions → New repository secret):
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID_ME`.
5. Actions → **price-watch** → **Run workflow**. The first run sends a summary with the top 5
   of every watch; after that only drops and the 09:00 / 21:00 summaries.

WhatsApp instead: secrets `PHONE_ME` + `CALLMEBOT_APIKEY_ME` (see
https://www.callmebot.com/blog/free-api-whatsapp-messages/). Telegram wins if both are set.

## Adding a watch
Add a block to [watches.yaml](watches.yaml) with either:
- `query: 9070 xt` – an Akakçe search, best for a specific model, or
- `url: https://www.akakce.com/power-supply/1000-w-power-supply.html` – an Akakçe category
  page with your filters selected, best for broad things (PSUs, SSDs, RAM...).

## Adding a person
She presses Start on the same bot; run `telegram_setup` again to see her chat id. Add the
secret `TELEGRAM_CHAT_ID_GF`, map it in [.github/workflows/price-watch.yml](.github/workflows/price-watch.yml)
under `env:`, and use `owner: gf` on her watches.

## Local
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pricebot.main --dry-run            # what a run would send
.venv\Scripts\python -m pricebot.main --dry-run --summary  # force a summary
```
