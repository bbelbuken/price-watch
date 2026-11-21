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

## Telegram menu (Cloudflare Worker, free)
The button menu runs in a free Cloudflare Worker, so it answers **instantly**; `/menu` (or `0`) opens it:

- 🏠 **Ana menü**: ➕ Yeni takip · 📋 Takiplerim · 📊 Özet gönder · ❓ Yardım
- a watch: 🎯 Hedef · 💰 Fiyat aralığı · 🚫 Hariç kelimeler · 🏪 Satıcılar · 📊 Şimdiki fiyatlar · 🗑 Takibi sil
- everything that can be set can be removed on the same screen; every screen has ↩ Geri and 🏠 Ana menü
- 🏪 Satıcılar: tap sellers to select ✅ / unselect ▫️ – their own top 5 comes under the general one

Nobody sees or edits anyone else's watches; messages from unknown chats are ignored.
Shortcuts: `/ekle iphone 17 hedef 60000 hariç kılıf, pro`, `/liste`, `/ozet`.

**Timing**
- menu screens and every change: instant (the Worker stores the watch list in D1)
- a new watch: its top 5 within ~1 min ("👀 Takip başladı"). A GitHub run picks the search
  words and a minimum price (60 % of the typical price) so cases and "uyumlu" accessories don't
  fill the list – if nothing matches, it deletes the watch and says so
- 📊 Şimdiki fiyatlar / 📊 Özet: ~1 min (a GitHub run, "telegram" in Actions)
- after you change a watch (words, range, sellers): the next price run (≤ 10 min) sends its new
  list **once** – several changes in a row still give one message
- 🏪 Satıcılar opens instantly: price runs keep each watch's seller list up to date

**How it fits together**
```
Telegram ⇄ Worker (menu, watch list in D1) ──job──▶ GitHub run "telegram" (Akakçe work, ~1 min)
                       ▲
GitHub run "price-watch" (every 10 min): reads the list, checks prices, writes back
cheapest price + seller lists, keeps watches.yaml as a readable copy (committed)
```
Price runs and Telegram runs queue separately and never wait for each other; only price runs
use the price memory (state.json).

### Setup (once, ~15 min)
1. **A random string** for `INBOX_TOKEN` (keep it):
   ```
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
2. **GitHub token for the Worker**: GitHub → Settings → Developer settings → Fine-grained tokens →
   only `price-watch`, *Actions: Read and write* (the cron-job.org one works too).
3. **Cloudflare** (free account) → *Storage & Databases → D1* → Create database `pricewatch` →
   *Console* → paste [cloudflare/schema.sql](cloudflare/schema.sql) → Execute.
4. *Workers & Pages → Create → Worker* (name e.g. `pricewatch-bot`) → Deploy → *Edit code* →
   replace everything with [cloudflare/worker.js](cloudflare/worker.js) → Deploy.
5. Worker → *Settings → Bindings → Add → D1 database*: variable name `DB`, database `pricewatch`.
   *Settings → Variables and Secrets*:
   - secrets: `TELEGRAM_TOKEN` (bot token), `INBOX_TOKEN` (step 1), `GITHUB_TOKEN` (step 2)
   - text: `GITHUB_REPO` = `bbelbuken/price-watch`, `OWNERS` = `me:<your chat id>,gf:<her chat id>`
6. **GitHub Secrets**: `INBOX_URL` = `https://<worker>.workers.dev`, `INBOX_TOKEN` = step 1.
   Then run the workflow once (Actions → price-watch → Run workflow): it copies watches.yaml into
   the Worker and prepares the seller lists.
7. On your PC: `python -u -m pricebot.telegram_setup --webhook https://<worker>.workers.dev/telegram`
   → add the secret it prints to the Worker as `TELEGRAM_SECRET`.

Check: send `/menu` – the menu appears at once. After that, edit watches in the menu, not in
watches.yaml (the Worker's list wins; watches.yaml is overwritten with it on every price run).
Undo: `telegram_setup --no-webhook` and delete the `INBOX_URL` secret – price runs then use
watches.yaml again and there is no menu.

## watches.yaml fields
- `id` unique name, `owner` who gets it (`me`, `gf` → secret `TELEGRAM_CHAT_ID_<OWNER>`)
- exactly one of `query` (Akakçe search text) or `url` (Akakçe category page, filters selected –
  best for broad things like PSUs); optional `name` label
- `include` (all must appear in the name), `exclude` (none may appear) – spaces/case ignored
- `min_price`, `max_price`, `target` (🔥 at or below), `min_drop_pct` (default 1)
- `sellers` – chosen sellers; adds a second top 5 of only their offers (product pages show the
  cheapest ~16 offers, the 10 cheapest products are checked)
- `settings.summary_times` – summary times, Turkey time

## Adding a person
She presses Start on the same bot; her chat id comes from @userinfobot. Add the secret
`TELEGRAM_CHAT_ID_GF` (mapped in [.github/workflows/price-watch.yml](.github/workflows/price-watch.yml)
under `env:`) and add `gf:<chat id>` to the Worker's `OWNERS`. She then adds her watches in the menu.

## Local
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pricebot.main --dry-run            # what a run would send
.venv\Scripts\python -m pricebot.main --dry-run --summary  # force a summary
node tests/worker.test.mjs                                 # the Telegram menu (Worker)
```
