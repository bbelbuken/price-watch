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
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import akakce, notify
from .matching import ranked

ROOT = Path(__file__).resolve().parent.parent
WATCHES_FILE = ROOT / "watches.yaml"
STATE_FILE = ROOT / "state.json"

TURKEY = timezone(timedelta(hours=3))  # no DST in Turkey since 2016
REQUEST_PAUSE_S = 3  # politeness pause between requests to Akakçe
RETRY_WAITS_S = (10, 30)  # 3 tries in total per run
TOP_N = 5
NAME_MAX = 48
DEFAULTS = {"summary_times": ["09:00", "21:00"], "min_drop_pct": 1.0}


# ---------------------------------------------------------------- formatting

def tl(amount: float) -> str:
    """61900.5 -> '61.900,50 TL'; whole numbers without decimals."""
    whole = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return whole.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def short(name: str) -> str:
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 1].rstrip() + "…"


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

def load_config(path: Path = WATCHES_FILE) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = {**DEFAULTS, **(data.get("settings") or {})}
    watches = data.get("watches") or []
    for w in watches:
        for f in ("id", "owner"):
            if not w.get(f):
                raise ValueError(f"watch {w!r} is missing '{f}'")
        if bool(w.get("query")) == bool(w.get("url")):
            raise ValueError(f"watch '{w['id']}' needs exactly one of 'query' or 'url'")
        if w.get("url"):
            akakce.category_url(w["url"])  # raises ValueError for non-category links
    ids = [w["id"] for w in watches]
    if len(ids) != len(set(ids)):
        raise ValueError("watch ids must be unique")
    return settings, watches


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

def product_line(i: int, p: akakce.Product, watch: dict, drop: Drop | None) -> str:
    target = watch.get("target")
    fire = "🔥" if target is not None and p.price <= float(target) else ""
    line = f"{i}. {fire}{tl(p.price)}"
    if drop and drop.before is None:
        line += " 🆕 ilk 5'e girdi"
    elif drop:
        pct = (drop.before - p.price) / drop.before * 100
        line += f" ⬇️ %{pct:.0f} ({tl(drop.before)})"
    return f"{line}\n   {short(p.name)}"


def watch_block(res: WatchResult, products: list[akakce.Product], seller: str) -> str:
    w = res.watch
    head = f"*{label(w)}*"
    if w.get("target") is not None:
        head += f" · hedef {tl(float(w['target']))}"
    drops = {d.product.code: d for d in res.drops}
    lines = [head]
    for i, p in enumerate(products, 1):
        lines.append(product_line(i, p, w, drops.get(p.code)))
        if i == 1 and seller:
            lines.append(f"   🏪 {seller}")
    lines.append(f"Daha fazla: {search_url(w)}")
    return "\n".join(lines)


def build_message(results: list[WatchResult], summary_slot: str | None, sellers: dict) -> tuple[str | None, list]:
    """Return (text, [(result, products_reported)]) for one owner; text None if nothing to say."""
    blocks, reported = [], []
    for res in results:
        if res.error:
            if res.warn or summary_slot:
                blocks.append(f"⚠️ *{label(res.watch)}*: Akakçe 3 denemede okunamadı ({res.error}).")
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
    if summary_slot:
        hour = int(summary_slot[-5:-3])
        title = ("☀️ Sabah özeti" if hour < 15 else "🌙 Akşam özeti") + f" · {summary_slot[-5:]}"
    elif any(d.before is not None for r, _ in reported for d in r.drops):
        title = "📉 Fiyat düştü!"
    elif any(r.drops for r, _ in reported):
        title = "🆕 İlk 5'e yeni ürün girdi"
    elif any(r.is_new for r, _ in reported):
        title = "👀 Takip başladı"
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


def check(watch: dict, state: dict, settings: dict) -> WatchResult:
    entry = state.setdefault("watches", {}).setdefault(watch["id"], {})
    try:
        products = fetch(watch)
    except akakce.AkakceError as exc:
        res = WatchResult(watch, error=str(exc), warn=not entry.get("warned"))
        entry["warned"] = True
        return res
    res = evaluate(watch, ranked(products, watch), entry, float(settings["min_drop_pct"]))
    if entry.pop("warned", False):
        res.recovered = True
    print(f"[{watch['id']}] {len(res.top)} shown, {len(res.drops)} drops, new={res.is_new}, "
          f"cheapest={tl(res.top[0].price) if res.top else '-'}")
    return res


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print, don't send or save")
    parser.add_argument("--summary", action="store_true", help="force a summary now")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    settings, watches = load_config()
    state = load_state()
    by_owner: dict[str, list[WatchResult]] = {}
    for i, watch in enumerate(watches):
        if i:
            time.sleep(REQUEST_PAUSE_S)
        by_owner.setdefault(watch["owner"], []).append(check(watch, state, settings))

    summaries = state.setdefault("summaries", {})
    for owner, results in by_owner.items():
        slot = due_summary_slot(now, settings["summary_times"], summaries.get(owner))
        if args.summary:
            slot = now.astimezone(TURKEY).strftime("%Y-%m-%d %H:%M")
        # Seller of the cheapest product, only for watches that will be in the message.
        sellers = {
            r.watch["id"]: best_seller(r.top[0])
            for r in results
            if r.top and (slot or r.is_new or r.drops)
        }
        text, reported = build_message(results, slot, sellers)
        if text is None:
            continue
        if not notify.send(owner, text, args.dry_run):
            continue  # nothing marked as told -> retried next run
        for res, products in reported:
            mark_reported(state["watches"][res.watch["id"]], products)
        if slot:
            summaries[owner] = slot

    # Forget watches removed from watches.yaml.
    ids = {w["id"] for w in watches}
    state["watches"] = {k: v for k, v in state.get("watches", {}).items() if k in ids}
    if not args.dry_run:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
