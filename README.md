# price-watch

Hourly price bot: searches [Akakçe](https://www.akakce.com) for each watch in
[watches.yaml](watches.yaml) and sends a WhatsApp message (via CallMeBot) when the
cheapest matching product gets cheaper. Runs free on GitHub Actions. Plan: [price-bot-plan.md](price-bot-plan.md).

## Setup
1. **CallMeBot** (once per person, from their own phone): follow
   https://www.callmebot.com/blog/free-api-whatsapp-messages/ – add their number to contacts,
   send the activation message, wait for the API key.
2. **GitHub Secrets** (repo → Settings → Secrets and variables → Actions):
   `PHONE_ME` (e.g. `+905551112233`) and `CALLMEBOT_APIKEY_ME`.
3. Push, then Actions → **price-watch** → **Run workflow**. You get one "Takip başladı"
   message per watch; after that only price drops.

## Adding a person
Use a new `owner` name in watches.yaml (e.g. `owner: gf`), add secrets
`PHONE_GF` + `CALLMEBOT_APIKEY_GF`, and map them in
[.github/workflows/price-watch.yml](.github/workflows/price-watch.yml) under `env:`.

## Local
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pricebot.main --dry-run
```
