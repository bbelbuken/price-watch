"""One run (every ~10 min): search every watch on Akakçe, then send each person
at most ONE WhatsApp message:

* Drop alert  – only the products that got >= min_drop_pct cheaper since we last
                told the owner, plus newly added watches.
* Summary     – morning/evening (settings.summary_times, Turkey time): the top 5
                of every watch, with drops marked. Replaces the alert in that run.
* Warnings    – a watch that couldn't be read after 3 tries (once, until it recovers).

Usage:
    python -m pricebot.main                       # real run
    python -m pricebot.main --dry-run             # print, don't send or save
    python -m pricebot.main --dry-run --summary   # force a summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import akakce, notify
from .matching import compact, ranked

ROOT = Path(__file__).resolve().parent.parent
WATCHES_FILE = ROOT / "watches.yaml"
STATE_FILE = ROOT / "state.json"

TURKEY = timezone(timedelta(hours=3))  # no DST in Turkey since 2016
REQUEST_PAUSE_S = 3  # politeness pause between requests to Akakçe
RETRY_WAITS_S = (10, 30)  # 3 tries in total per run
TOP_N = 5
NAME_MAX = 30
DEFAULTS = {"summary_times": ["09:00", "21:00"], "min_drop_pct": 1.0}


# ---------------------------------------------------------------- formatting

def tl(amount: float) -> str:
    """61900.5 -> '61.900,50 TL'; whole numbers without decimals."""
    whole = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return whole.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def lira(amount: float) -> str:
    """Compact price for lists: 63479.08 -> '63.479₺' (kuruş dropped)."""
    return f"{round(amount):,}".replace(",", ".") + "₺"


# Words that add nothing in a phone-sized list (GPU boilerplate mostly).
_FILLER = {"rx", "amd", "radeon", "ekran", "karti", "edition"}
# Specs that start the "technical tail" of a name: "256 Bit GDDR6 16 GB Ekran Kartı".
_TAIL_RE = re.compile(r"\s(\d+\s?bit|gddr\d\w*)", re.IGNORECASE)  # phones keep "256 GB"


def _is_part_number(token: str) -> bool:
    """GV-R9070XTGAMING, OC-16GD, 11348-03-20G, PRIME-RX9070XT-O16G – codes, not words."""
    has_digit = any(c.isdigit() for c in token)
    has_alpha = any(c.isalpha() for c in token)
    if "-" in token:
        return has_digit and len(token) >= 6
    return has_digit and has_alpha and len(token) >= 10  # keeps model names like CSK1000


def short_name(name: str, watch: dict) -> str:
    """'Gigabyte RX 9070 XT Gaming OC GV-R9070XTGAMING OC-16GD 256 Bit GDDR6 16 GB Ekran Kartı'
    -> 'Gigabyte Gaming OC' (search words, part numbers and the spec tail removed)."""
    query_words = {compact(w) for w in str(watch.get("query") or watch.get("name") or "").split()}
    head = _TAIL_RE.split(name, maxsplit=1)[0]
    words = [
        w for w in head.split()
        if compact(w) not in query_words and compact(w) not in _FILLER and not _is_part_number(w)
    ]
    words = words or name.split()
    text = " ".join(words)
    if len(text) <= NAME_MAX:
        return text
    out = ""
    for w in words:  # cut at a word boundary
        if len(out) + len(w) + 1 > NAME_MAX - 1:
            break
        out = f"{out} {w}".strip()
    return (out or text[: NAME_MAX - 1]) + "…"


def seller_name(seller: str) -> str:
    return re.sub(r"\.(com|net)(\.tr)?$", "", seller, flags=re.IGNORECASE)


def search_url(watch: dict) -> str:
    """The same view the bot uses: cheapest first (within min/max price for searches)."""
    if watch.get("url"):
        return akakce.category_url(watch["url"])
    return akakce.search_url(watch["query"], watch.get("min_price"), watch.get("max_price"))


def label(watch: dict) -> str:
    """Name shown in messages: `name`, else the search text, else the category slug."""
    if watch.get("name") or watch.get("query"):
        return str(watch.get("name") or watch["query"])
    slug = watch["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1].split(",")[0]
    return slug.removesuffix(".html").replace("-", " ")


# ---------------------------------------------------------------- config / state

def validate_watch(w: dict) -> None:
    for f in ("id", "owner"):
        if not w.get(f):
            raise ValueError(f"watch {w!r} is missing '{f}'")
    if bool(w.get("query")) == bool(w.get("url")):
        raise ValueError(f"watch '{w['id']}' needs exactly one of 'query' or 'url'")
    if w.get("url"):
        akakce.category_url(w["url"])  # raises ValueError for non-category links


def load_config(path: Path = WATCHES_FILE) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = {**DEFAULTS, **(data.get("settings") or {})}
    watches = data.get("watches") or []
    for w in watches:
        validate_watch(w)
    ids = [w["id"] for w in watches]
    if len(ids) != len(set(ids)):
        raise ValueError("watch ids must be unique")
    return settings, watches


def load_watches() -> tuple[dict, list[dict], dict, bool]:
    """(settings, watches, metas, from_worker). With the Cloudflare Worker the watch list
    lives there (the menu edits it); the first time, watches.yaml is imported into it."""
    settings, yaml_watches = load_config()
    if not notify.worker_enabled():
        return settings, yaml_watches, {}, False
    rows = notify.watches_fetch()
    if rows is None:
        print("Worker unreachable – using watches.yaml (the last copy) this time")
        return settings, yaml_watches, {}, False
    if not rows and yaml_watches:
        notify.watches_import(yaml_watches)
        rows = [{"id": w["id"], "body": w, "meta": {}} for w in yaml_watches]
    watches, metas = [], {}
    for r in rows:
        try:
            validate_watch(r["body"])
        except ValueError as exc:
            print(f"skipping a broken watch: {exc}")
            continue
        watches.append(r["body"])
        metas[r["id"]] = r.get("meta") or {}
    return settings, watches, metas, True


CONFIG_HEADER = """\
# Watch list – also edited by the bot when you use Telegram commands (/ekle, /sil, ...).
# You can still edit it by hand; field reference: README.md ("watches.yaml fields").
# Pull before editing locally (git pull): the bot commits its changes to this file.

"""


def save_config(settings: dict, watches: list[dict], path: Path = WATCHES_FILE) -> None:
    custom = {k: v for k, v in settings.items() if k in DEFAULTS}
    body = yaml.safe_dump(
        {"settings": custom, "watches": watches},
        allow_unicode=True, sort_keys=False, default_flow_style=None, width=100,
    )
    path.write_text(CONFIG_HEADER + body, encoding="utf-8")


def load_state(path: Path = STATE_FILE) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            print("state.json unreadable, starting fresh")
    return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- core logic (pure)

@dataclass
class Drop:
    product: akakce.Product
    before: float | None  # price we last told the owner about; None = never seen, just entered the top 5


@dataclass
class WatchResult:
    watch: dict
    top: list[akakce.Product] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    is_new: bool = False
    error: str | None = None
    warn: bool = False  # first failure after it was fine -> tell the owner
    recovered: bool = False  # works again after a warning
    fp: str = ""  # fingerprint of the watch's settings when checked
    matching: list = field(default_factory=list)  # all matching products, cheapest first


FORGET_AFTER_DAYS = 7  # products not seen this long are forgotten


def evaluate(
    watch: dict,
    matching: list[akakce.Product],
    entry: dict,
    min_drop_pct: float,
    today: str | None = None,
) -> WatchResult:
    """Compare fresh results with the state entry and update the entry's prices.

    Only the current top 5 (cheapest matching) can trigger an alert:
    * a known product whose price is >= min_drop_pct below what the owner last heard, or
    * a product we have never seen that is now in the top 5 (🆕).
    entry["items"][code] = {"price", "ref" (price the owner last heard about), "seen" (date)}.
    `ref` follows the price up (so a later fall is a drop), but only moves down when the
    owner is told (mark_reported) – slow creeping drops add up instead of being lost.
    Products are remembered for FORGET_AFTER_DAYS even when they drop off the page, so
    one coming back isn't reported as new.
    """
    today = today or datetime.now(TURKEY).date().isoformat()
    items: dict = entry.setdefault("items", {})
    res = WatchResult(watch, top=matching[:TOP_N], is_new=not items)
    pct = float(watch.get("min_drop_pct", min_drop_pct))

    if not res.is_new:
        for p in res.top:
            old = items.get(str(p.code))
            if old is None:
                res.drops.append(Drop(p, None))
            elif p.price <= old["ref"] * (1 - pct / 100):
                res.drops.append(Drop(p, old["ref"]))

    for p in matching:
        old = items.get(str(p.code))
        ref = p.price if old is None else max(old["ref"], p.price)
        items[str(p.code)] = {"price": p.price, "ref": ref, "seen": today}
    cutoff = (datetime.fromisoformat(today) - timedelta(days=FORGET_AFTER_DAYS)).date().isoformat()
    entry["items"] = {k: v for k, v in items.items() if v.get("seen", today) >= cutoff}
    return res


def mark_reported(entry: dict, products: list[akakce.Product]) -> None:
    for p in products:
        item = entry["items"].get(str(p.code))
        if item:
            item["ref"] = p.price


def due_summary_slot(now: datetime, times: list[str], last_sent: str | None) -> str | None:
    """Latest summary slot (e.g. '2026-11-21 09:00') at or before `now` that wasn't sent yet."""
    local = now.astimezone(TURKEY)
    slots = []
    for day in (local.date() - timedelta(days=1), local.date()):
        for t in times:
            hh, mm = (int(x) for x in t.split(":"))
            slot = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TURKEY)
            if slot <= local:
                slots.append(slot)
    if not slots:
        return None
    latest = max(slots).strftime("%Y-%m-%d %H:%M")
    return None if latest == last_sent else latest


# ---------------------------------------------------------------- messages

LINK_PREFIX = "🔗 "  # notify.py turns these lines into buttons where it can (ntfy)


def product_line(p: akakce.Product, watch: dict, drop: Drop | None) -> str:
    """'61.900₺  Gigabyte Gaming OC' – one line; a drop adds a short second line."""
    target = watch.get("target")
    fire = "🔥" if target is not None and p.price <= float(target) else ""
    line = f"{fire}{lira(p.price)}  {short_name(p.name, watch)}"
    if p.seller:
        line += f" · {seller_name(p.seller)}"
    if drop and drop.before is None:
        return f"{line} 🆕"
    if drop:
        pct = (drop.before - p.price) / drop.before * 100
        return f"{line}\n      ⬇️ %{pct:.0f} · önce {lira(drop.before)}"
    return line


def watch_block(res: WatchResult, products: list[akakce.Product], seller: str) -> str:
    w = res.watch
    head = f"▸ *{label(w)}*"
    if w.get("target") is not None:
        head += f" · 🎯 {lira(float(w['target']))}"
    drops = {d.product.code: d for d in res.drops}
    lines = [head] + [product_line(p, w, drops.get(p.code)) for p in products]
    if seller:
        lines.append(f"🏪 En ucuz: {seller_name(seller)}")
    lines.append(f"{LINK_PREFIX}{search_url(w)}")
    return "\n".join(lines)


def build_message(
    results: list[WatchResult], summary_slot: str | None, sellers: dict, title: str | None = None
) -> tuple[str | None, list]:
    """Return (text, [(result, products_reported)]) for one owner; text None if nothing to say.
    `title` replaces the automatic one (with the time for "now" slots)."""
    blocks, reported = [], []
    for res in results:
        if res.error:
            if res.warn or summary_slot:
                blocks.append(f"⚠️ *{label(res.watch)}*: Akakçe okunamadı ({res.error})")
            continue
        if res.recovered:
            blocks.append(f"✅ *{label(res.watch)}* tekrar okunuyor.")
        if summary_slot or res.is_new:
            products = res.top
        else:
            products = [d.product for d in res.drops]
        if not products:
            continue
        blocks.append(watch_block(res, products, sellers.get(res.watch["id"], "")))
        reported.append((res, products))

    if not blocks:
        return None, []
    if title:
        title = f"{title} · {summary_slot[-5:]}" if summary_slot else title
    elif summary_slot and summary_slot.startswith("now "):
        title = f"📋 Özet · {summary_slot[-5:]}"
    elif summary_slot:
        hour = int(summary_slot[-5:-3])
        title = ("☀️ Sabah özeti" if hour < 15 else "🌙 Akşam özeti") + f" · {summary_slot[-5:]}"
    elif any(d.before is not None for r, _ in reported for d in r.drops):
        title = "📉 Fiyat düştü!"
    elif any(r.drops for r, _ in reported):
        title = "🆕 İlk 5'e yeni ürün girdi"
    elif any(r.is_new for r, _ in reported):
        title = "👀 Güncel liste"
    else:
        title = "ℹ️ Fiyat botu"
    return title + "\n\n" + "\n\n".join(blocks), reported


# ---------------------------------------------------------------- I/O

def fetch(watch: dict) -> list[akakce.Product]:
    """Search with up to 3 tries; raises AkakceError if all fail."""
    last_exc = None
    for attempt, wait in enumerate((0, *RETRY_WAITS_S), 1):
        if wait:
            time.sleep(wait)
        try:
            if watch.get("url"):
                return akakce.category(watch["url"])
            return akakce.search(watch["query"], watch.get("min_price"), watch.get("max_price"))
        except akakce.AkakceError as exc:
            last_exc = exc
            print(f"  try {attempt} failed: {exc}")
    raise last_exc


SELLER_CANDIDATES = 10  # product pages checked per run for a watch with chosen sellers
OFFER_CACHE_MIN = 60  # reuse a product's offers this long if its search price didn't change


def seller_allowed(seller: str, allowed: list[str]) -> bool:
    """'Amazon Türkiye' matches 'amazon'; 'Hepsiburada' matches 'hepsi burada'."""
    name = compact(seller)
    return any(compact(a) and compact(a) in name for a in allowed)


def cached_offers(product: akakce.Product, state: dict, now: float) -> list[dict]:
    """Offers of a product page as [{'seller', 'price'}], cached in state to save requests."""
    cache = state.setdefault("offers", {})
    hit = cache.get(str(product.code))
    if hit and hit["search_price"] == product.price and now - hit["t"] < OFFER_CACHE_MIN * 60:
        return hit["offers"]
    time.sleep(REQUEST_PAUSE_S)
    offers = [{"seller": o.seller, "price": o.price + o.ship_price} for o in akakce.offers(product.url)]
    cache[str(product.code)] = {"t": now, "search_price": product.price, "offers": offers}
    return offers


def apply_seller_filter(
    matching: list[akakce.Product], allowed: list[str], state: dict, now: float | None = None
) -> list[akakce.Product]:
    """Re-price the cheapest candidates with their cheapest offer from an allowed seller.

    Akakçe's search only shows each product's overall cheapest price; the product page
    lists the cheapest ~16 offers with seller names. Products without an allowed seller
    among those are dropped. Result: cheapest first, each with `seller` set."""
    now = now or time.time()
    result = []
    for p in matching[:SELLER_CANDIDATES]:
        try:
            offers = cached_offers(p, state, now)
        except akakce.AkakceError:
            continue
        good = [o for o in offers if seller_allowed(o["seller"], allowed)]
        if good:
            best = min(good, key=lambda o: o["price"])
            result.append(akakce.Product(p.code, p.name, best["price"], p.url, p.offer_count, best["seller"]))
    # Forget cached offers of products that are no longer candidates.
    keep = {str(p.code) for p in matching[:SELLER_CANDIDATES * 2]}
    state["offers"] = {k: v for k, v in state.get("offers", {}).items() if k in keep or now - v["t"] < 86400}
    return sorted(result, key=lambda p: p.price)


def best_seller(product: akakce.Product) -> str:
    try:
        time.sleep(REQUEST_PAUSE_S)
        offers = akakce.offers(product.url)
    except akakce.AkakceError:
        return ""
    if not offers:
        return ""
    o = offers[0]
    return o.seller if o.ship_price == 0 else f"{o.seller} (+{tl(o.ship_price)} kargo)"


SELLER_VIEW = "#satici"  # suffix of the state/watch id of a watch's "chosen sellers" list


def seller_view(watch: dict) -> dict:
    """The second list under a watch: top 5 among the chosen sellers only."""
    sellers = ", ".join(seller_name(s) for s in watch["sellers"])
    return {**watch, "id": watch["id"] + SELLER_VIEW, "name": f"{label(watch)} · {sellers}", "target": watch.get("target")}


FINGERPRINT_KEYS = ("query", "url", "include", "exclude", "min_price", "max_price", "sellers", "category")


def fingerprint(watch: dict) -> str:
    """Changes when anything that decides *which* products are listed changes (not the target)."""
    data = json.dumps({k: watch.get(k) for k in FINGERPRINT_KEYS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(data.encode()).hexdigest()[:12]


def _entry(state: dict, wid: str, fp: str) -> tuple[dict, bool]:
    """(price memory, fresh). Fresh = never seen or settings changed (e.g. from the menu):
    the memory starts over. Memory from before fingerprints existed is kept as is."""
    entries = state.setdefault("watches", {})
    entry = entries.get(wid)
    fresh = entry is None or entry.get("cfg") not in (None, fp)
    if fresh:
        warned = (entry or {}).get("warned")
        entry = entries[wid] = {"warned": True} if warned else {}
    entry["cfg"] = fp
    return entry, fresh


def check(watch: dict, state: dict, settings: dict, meta: dict | None = None) -> list[WatchResult]:
    """The overall top 5 – plus, if the owner picked sellers, a second top 5 of just those.

    After a change (new watch, or settings changed in the menu) the list is sent once, in the
    first run that sees it – unless exactly that version was already shown (meta["shown_fp"]:
    the Telegram run shows a new watch right away)."""
    meta = meta or {}
    fp = fingerprint(watch)
    entry, fresh = _entry(state, watch["id"], fp)
    try:
        products = fetch(watch)
    except akakce.AkakceError as exc:
        res = WatchResult(watch, error=str(exc), warn=not entry.get("warned"))
        entry["warned"] = True
        return [res]
    pct = float(settings["min_drop_pct"])
    matching = ranked(products, watch)
    results = [evaluate(watch, matching, entry, pct)]
    if entry.pop("warned", False):
        results[0].recovered = True
    results[0].matching = matching
    if watch.get("sellers"):
        view = seller_view(watch)
        view_entry, _ = _entry(state, view["id"], fp)
        if fresh:
            view_entry.pop("items", None)
        results.append(evaluate(view, apply_seller_filter(matching, watch["sellers"], state), view_entry, pct))
    for res in results:
        res.fp = fp
        res.is_new = fresh and meta.get("shown_fp") != fp
        print(f"[{res.watch['id']}] {len(res.top)} shown, {len(res.drops)} drops, new={res.is_new}, "
              f"cheapest={tl(res.top[0].price) if res.top else '-'}")
    return results


def watch_meta(res: WatchResult, state: dict) -> dict:
    """What the Worker's menu shows for a watch: cheapest price and its sellers (🏪 screen)."""
    from . import commands  # noqa: PLC0415
    if res.error or not res.matching:
        return {}
    options = commands.seller_options(res.matching, lambda p: cached_offers(p, state, time.time()))
    return {"cheapest": res.matching[0].price, "seller_options": options[:24]}


def _sellers_for(results: list[WatchResult], slot: str | None) -> dict:
    """Seller of the cheapest product, only for watches that will be in the message."""
    return {
        r.watch["id"]: best_seller(r.top[0])
        for r in results
        if r.top and not r.watch["id"].endswith(SELLER_VIEW) and (slot or r.is_new or r.drops)
    }


# ---------------------------------------------------------------- jobs from the Telegram menu

def send_list(owner: str, watches: list[dict], state: dict, settings: dict, title: str, dry_run: bool) -> list:
    """Send the current top 5 of `watches` right now. Returns the check results."""
    results = []
    for i, w in enumerate(watches):
        if i:
            time.sleep(REQUEST_PAUSE_S)
        results += check(w, state, settings)
    slot = "now " + datetime.now(TURKEY).strftime("%H:%M")
    text, _ = build_message(results, slot, _sellers_for(results, slot), title=title)
    if text:
        notify.send(owner, text, dry_run)
    return results


def run_jobs(settings: dict, watches: list[dict], dry_run: bool) -> None:
    """Work the menu handed over (it needs Akakçe): set up a new watch, prices now, summary,
    seller list. Uses a throwaway price memory – the price runs' memory is never touched."""
    from . import commands  # noqa: PLC0415

    jobs = notify.jobs_fetch()
    state: dict = {}
    metas: dict[str, dict] = {}
    for job in jobs:
        owner, chat = job.get("owner", ""), str(job.get("chat", ""))
        w = next((x for x in watches if x["id"] == job.get("watch") and x["owner"] == owner), None)
        print(f"[job] {job.get('type')} {owner} {job.get('watch', '')}")
        try:
            if job.get("type") == "summary":
                mine = [x for x in watches if x["owner"] == owner]
                if mine:
                    send_list(owner, mine, state, settings, "📋 Özet", dry_run)
                elif chat:
                    notify.telegram_reply(chat, "Henüz takibin yok. /menu → ➕ Yeni takip")
            elif not w:
                continue  # deleted meanwhile
            elif job["type"] == "setup":
                matching = commands.setup_watch(w, fetch)
                if not matching:
                    notify.watches_delete([w["id"]])
                    notify.telegram_reply(chat, f"❌ '{label(w)}' için ürün bulamadım, takibi sildim. "
                                                "Farklı kelimelerle tekrar dene: /menu → ➕ Yeni takip")
                    continue
                notify.watches_put([w])
                res = send_list(owner, [w], state, settings, "👀 Takip başladı", dry_run)[0]
                metas[w["id"]] = {"setup": "done", "shown_fp": fingerprint(w), **watch_meta(res, state)}
            elif job["type"] == "show":
                send_list(owner, [w], state, settings, "📊 Şimdiki fiyatlar", dry_run)
            elif job["type"] == "sellers":
                res = check(w, state, settings)[0]
                metas[w["id"]] = watch_meta(res, state)
                notify.telegram_reply(chat, f"🏪 {label(w)} – satıcı listesi hazır.",
                                      [[("🏪 Satıcıları aç", f"s:{w['id']}")]])
        except akakce.AkakceError as exc:
            if chat:
                notify.telegram_reply(chat, f"Akakçe'ye şu an ulaşılamadı ({exc}). En geç 10 dakika "
                                            "içinde bir sonraki kontrolde tekrar denerim.")
        except Exception as exc:  # one bad job must not block the others
            print(f"job failed: {type(exc).__name__}: {exc}")
    notify.watches_meta(metas)
    notify.jobs_ack([j["id"] for j in jobs])


# ---------------------------------------------------------------- main

COMMANDS_MENU_VERSION = 4  # bump when commands.BOT_COMMANDS changes, so the '/' menu is refreshed


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print, don't send or save")
    parser.add_argument("--summary", action="store_true", help="force a summary now")
    parser.add_argument("--mode", choices=("all", "commands"), default="all",
                        help="commands: only do the work the Telegram menu handed over (fast)")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    settings, watches, metas, from_worker = load_watches()
    if args.mode == "commands":
        # Telegram runs never load or save the price memory (state.json), so they can run
        # next to a price run.
        if from_worker and not args.dry_run:
            run_jobs(settings, watches, args.dry_run)
            export_watches(settings, watches)
        return 0

    state = load_state()
    if from_worker and not args.dry_run and state.get("commands_menu") != COMMANDS_MENU_VERSION:
        from . import commands  # noqa: PLC0415
        if notify.telegram_set_commands(commands.BOT_COMMANDS):
            state["commands_menu"] = COMMANDS_MENU_VERSION

    by_owner: dict[str, list[WatchResult]] = {}
    new_metas: dict[str, dict] = {}
    for i, watch in enumerate(watches):
        if i:
            time.sleep(REQUEST_PAUSE_S)
        results = check(watch, state, settings, metas.get(watch["id"]))
        if from_worker:
            new_metas[watch["id"]] = watch_meta(results[0], state)
        by_owner.setdefault(watch["owner"], []).extend(results)

    summaries = state.setdefault("summaries", {})
    for owner, results in by_owner.items():
        slot = due_summary_slot(now, settings["summary_times"], summaries.get(owner))
        if args.summary and not slot:
            slot = "now " + now.astimezone(TURKEY).strftime("%H:%M")
        text, reported = build_message(results, slot, _sellers_for(results, slot))
        if text is None:
            continue
        if not notify.send(owner, text, args.dry_run):
            continue  # nothing marked as told -> retried next run
        for res, products in reported:
            mark_reported(state["watches"][res.watch["id"]], products)
            if res.is_new:  # this version's list has now been shown once
                new_metas.setdefault(res.watch["id"].removesuffix(SELLER_VIEW), {})["shown_fp"] = res.fp
        if slot and not slot.startswith("now "):
            summaries[owner] = slot

    # Forget watches that no longer exist.
    ids = {w["id"] for w in watches} | {w["id"] + SELLER_VIEW for w in watches if w.get("sellers")}
    state["watches"] = {k: v for k, v in state.get("watches", {}).items() if k in ids}
    if not args.dry_run:
        save_state(state)
        if from_worker:
            notify.watches_meta({k: v for k, v in new_metas.items() if v})
            export_watches(settings, watches)
    return 0


def export_watches(settings: dict, watches: list[dict]) -> None:
    """Keep watches.yaml as a readable copy of the Worker's list (the workflow commits it)."""
    try:
        current = load_config()[1]
    except (OSError, ValueError):
        current = None
    if current != watches:
        save_config(settings, watches)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
