# Price bot – plan

A bot that checks prices on the stores we care about every hour and sends a WhatsApp
message when a price drops. Built for my new-PC hunt, so I stop checking sites by hand.
Personal use, built with Claude Code. Starter code exists (fiyat-bot.zip, includes CLAUDE.md).

## Requirements
1. **100% free** – no paid APIs, servers or messages.
2. **WhatsApp** for both alerts and commands.
3. **Self-service:** my girlfriend (Gökçe) can add any site/product she wants herself,
   without me editing code.
4. **Multiple phones:** works with different WhatsApp numbers, not just mine.
5. **Per-user separation:** each person only sees and gets alerts for their own watches.
6. **Example:** I track "9070 XT" on Amazon + Hepsiburada; Gökçe sends a perfume name for
   Sephora and gets its price back as a message.
7. No always-on PC – everything runs in free cloud tiers.

## Architecture

| Part | Choice |
| --- | --- |
| Language | Python 3.12 (requests, beautifulsoup4, PyYAML) for the scraper |
| Scheduler | GitHub Actions cron, every hour at :17 (private repo) |
| Alerts (outgoing) | WhatsApp via CallMeBot free API – each user activates it with their own number and gets their own API key |
| Commands (incoming) | WhatsApp Cloud API (Meta) free test number → webhook on a Cloudflare Worker (free tier) |
| Watch storage | Cloudflare KV (free), keyed by user; Actions reads it through a token-protected Worker endpoint |
| Users | Allow-list in a Worker secret: phone number → user name (me, Gökçe); unknown numbers are ignored |
| Price memory | state.json – committed back to the repo by the workflow on each run |
| Secrets | Per user: phone + CALLMEBOT\_APIKEY\_<USER>; Meta token, verify token, KV/Worker token |
| Fallback | Shared Google Sheet (one tab per user) read as published CSV, if the Meta route fails |

### Flow
1. Gökçe texts the bot number: `add sephora Chanel Coco Mademoiselle 100ml 4000`.
2. Meta calls the Worker webhook → Worker looks up her number → saves the watch under
   `gokce` in KV → replies "added ✅" (free: reply inside the 24h window).
3. Every hour GitHub Actions pulls all users' watches, scrapes, compares with state.json.
4. On a price drop, the bot sends the alert to *that user's* number via *their* CallMeBot key.

### WhatsApp commands
- `add <site> <search or URL> [target price]` – sites: amazon, hepsiburada, akakce, sephora,
  or any product URL
- `list` – my watches with last seen price
- `remove <no>` – delete a watch
- `price <no>` – check now and reply with current price
- `help`

### Site support
- Main parser: **Akakçe** (search by product name → cheapest offer + seller, e.g. Amazon,
  Hepsiburada, a perfume shop). Amazon, Hepsiburada and Sephora block GitHub's servers
  (see "Checks done"), so their prices come via Akakçe.
- **Generic "any URL" parser:** reads price from JSON-LD `Product/Offer`, `product:price:amount`
  / `og:price` meta tags; optional CSS selector override. This is how Gökçe can add a site
  we didn't write a parser for.

## Checks done (2026-11-20)

**Meta WhatsApp Cloud API pricing** (Meta's pricing page, changes effective 2026-10-01):
- Free-form replies inside the 24h window are now charged, **but each number gets 1,000 free
  service messages per month**. Two users sending a few commands a day stays far below that → €0.
- Utility *templates* inside the window are charged again → the bot must reply with plain text,
  never templates. Proactive alerts stay on CallMeBot.
- Meta: businesses with **no payment method on file** had service messages stopped from
  2026-10-01. It's unclear whether that applies to the free test number. If Meta asks for a
  card, the bot is still €0 under the free tier, but a card has to be on file → if that's not
  acceptable, use the Google Sheet fallback.
- Test number: free messages to up to 5 added recipient numbers (enough for us). Use a
  permanent System User token, not the 24h temporary token.

**Site access** (plain HTTP request from my home PC, not yet from GitHub's servers):

| Site | Result | Meaning |
| --- | --- | --- |
| Amazon TR (search) | 200, full results page | ✅ works |
| Akakçe (search) | 200, full results page | ✅ works |
| Hepsiburada | 403 "Güvenlik" page, even the home page | ❌ blocked even from home |
| Sephora TR (search) | 403 "Access Denied" | ❌ blocked even from home |

Consequences:
- Hepsiburada and Sephora prices will come **through Akakçe** (it lists Hepsiburada offers and
  many cosmetics/perfume sellers) instead of scraping those sites directly. No bot-protection
  bypassing.
- Sephora "search by perfume name" becomes: search the name on Akakçe, report the cheapest
  offer (and the Sephora offer if Akakçe lists one).

**Site access from GitHub Actions** (test workflow on branch `test/site-access`, 2026-11-20):

| Site | Result | Meaning |
| --- | --- | --- |
| Akakçe "9070 xt" | 200, full results page | ✅ works from GitHub |
| Akakçe "chanel coco mademoiselle" | 200, full results page | ✅ perfume search works too |
| Amazon TR | 503 (Amazon's bot-block page) | ❌ blocked from GitHub (works from home) |
| Hepsiburada | 403 "Güvenlik" | ❌ blocked |
| Sephora TR | not measured (test step stopped early); blocked from home already | ❌ |

**Decision: Akakçe is the single price source** in the cloud. It lists offers from Amazon,
Hepsiburada and many perfume/cosmetics sellers, so one parser covers both users. Direct
Amazon/Hepsiburada/Sephora parsers are dropped (no PC to run them from, no bot-protection bypass).
Also: the GitHub repo is currently **public** – fine as long as secrets stay in GitHub Secrets;
public repos also get free unlimited Actions minutes.

## To-do
- [ ] CallMeBot activation for **each user**: add the number to contacts, send the
      activation message, get the API key
- [ ] Meta developer account → WhatsApp Cloud API app → free test number; add both of our
      numbers as allowed recipients
- [x] Verify current Meta pricing → 1,000 free service messages/month per number; plain-text replies only
- [ ] Check whether Meta requires a payment method for the test number (if yes and not OK → Google Sheet fallback)
- [x] Test-run a fetch workflow on GitHub Actions → only Akakçe works; Akakçe becomes the main source
- [ ] Cloudflare Worker: webhook verify + command parser + KV storage + `/watches` endpoint
- [ ] Private GitHub repo, upload the zip contents
- [ ] Secrets: phones, CALLMEBOT\_APIKEY\_<USER>, Meta token, Worker token
- [ ] Refactor watches.yaml → watches fetched from the Worker, each with an `owner`
- [ ] Sephora TR parser + generic URL parser
- [ ] First test: Actions → price-watch → Run workflow
- [ ] Check that all sites are readable from GitHub's servers (if Amazon/Sephora return a
      captcha, mark that site as unsupported from the cloud – no PC fallback)
- [ ] Targets for me: 9070 XT 58,000 TL, 9070 50,000 TL
- [ ] Gökçe onboarding: CallMeBot key + send `help` to the bot
- [ ] Optional: Trendyol, n11, Vatan, İncehesap parsers; daily summary message; pytest with HTML fixtures

## Rules
- One request per site per hour, 3 s pause between requests, no captcha bypassing
- First run: one message per watch; after that only when the price drops; 🔥 if below target
- If a site can't be read 3 times in a row: one warning message to the watch owner
- Alerts only go to the watch's owner; users can't see each other's watches
- Only allow-listed numbers can use commands
- Secrets and phone numbers never go into the repo

## Known risks
- Sites change their HTML → parser breaks → update selectors (notes in CLAUDE.md);
  generic parser reduces this
- GitHub IPs may be blocked (especially Amazon, possibly Sephora)
- CallMeBot is a free hobby service; it can be slow at times
- Meta could change WhatsApp pricing/test-number rules → switch commands to the Google Sheet fallback
- Store terms of service may restrict automated access; at personal, low-frequency use the
  practical risk is an IP block
