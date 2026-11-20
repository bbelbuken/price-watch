"""Hourly run: search every watch on Akakçe, alert the owner when the price drops.

Usage:
    python -m pricebot.main              # real run (sends WhatsApp, writes state.json)
    python -m pricebot.main --dry-run    # print messages, don't send or save
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from . import akakce, notify
from .matching import cheapest

ROOT = Path(__file__).resolve().parent.parent
WATCHES_FILE = ROOT / "watches.yaml"
STATE_FILE = ROOT / "state.json"

REQUEST_PAUSE_S = 3  # politeness pause between requests to Akakçe
FAIL_ALERT_AFTER = 3  # consecutive failures before one warning message
DEFAULT_MIN_DROP_PCT = 1.0  # ignore tiny price jitter


def tl(amount: float) -> str:
    """61900.5 -> '61.900,50 TL'; whole numbers without decimals."""
    whole = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return whole.replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def load_watches(path: Path = WATCHES_FILE) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    watches = data.get("watches") or []
    for w in watches:
        for field in ("id", "owner", "query"):
            if not w.get(field):
                raise ValueError(f"watch {w!r} is missing '{field}'")
    ids = [w["id"] for w in watches]
    if len(ids) != len(set(ids)):
        raise ValueError("watch ids must be unique")
    return watches


def load_state(path: Path = STATE_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def seller_line(product: akakce.Product) -> str:
    """Best-effort 'cheapest at <seller>' – one extra request, only when alerting."""
    try:
        time.sleep(REQUEST_PAUSE_S)
        offers = akakce.offers(product.url)
    except akakce.AkakceError:
        return ""
    if not offers:
        return ""
    best = offers[0]
    ship = "" if best.ship_price == 0 else f" + {tl(best.ship_price)} kargo"
    return f"\n🏪 {best.seller}{ship}"


def decide(watch: dict, product: akakce.Product, entry: dict) -> str | None:
    """Return the alert reason ('first', 'drop') or None. Pure: no I/O."""
    last = entry.get("price")
    if last is None:
        return "first"
    min_drop = float(watch.get("min_drop_pct", DEFAULT_MIN_DROP_PCT))
    if product.price < last * (1 - min_drop / 100):
        return "drop"
    return None


def message(watch: dict, product: akakce.Product, reason: str, last: float | None) -> str:
    target = watch.get("target")
    hot = target is not None and product.price <= float(target)
    head = "🔥 HEDEFİN ALTINDA! " if hot else ""
    if reason == "first":
        title = f"{head}👀 Takip başladı: {watch['query']}"
    else:
        diff = last - product.price
        title = f"{head}📉 Fiyat düştü: {watch['query']} (-{tl(diff)})"
    lines = [title, product.name, f"💰 {tl(product.price)}"]
    if reason == "drop":
        lines[-1] += f" (önce {tl(last)})"
    if target is not None:
        lines.append(f"🎯 Hedef: {tl(float(target))}")
    lines.append(product.url)
    return "\n".join(lines)


def run_watch(watch: dict, state: dict, dry_run: bool) -> None:
    wid, owner = watch["id"], watch["owner"]
    entry = state.setdefault(wid, {})
    try:
        products = akakce.search(watch["query"])
    except akakce.AkakceError as exc:
        entry["fails"] = entry.get("fails", 0) + 1
        print(f"[{wid}] fetch failed ({entry['fails']}x): {exc}")
        if entry["fails"] == FAIL_ALERT_AFTER:
            notify.send(
                owner,
                f"⚠️ '{watch['query']}' için Akakçe {FAIL_ALERT_AFTER} kez üst üste "
                f"okunamadı ({exc}). Düzelince tekrar haber veririm.",
                dry_run,
            )
        return
    entry["fails"] = 0

    product = cheapest(products, watch)
    if product is None:
        print(f"[{wid}] no matching product among {len(products)} results")
        return

    reason = decide(watch, product, entry)
    last = entry.get("price")
    print(f"[{wid}] cheapest {tl(product.price)} – {product.name} (last: {last}) -> {reason}")
    if reason:
        text = message(watch, product, reason, last) + seller_line(product)
        if not notify.send(owner, text, dry_run):
            return  # keep the old price so the alert is retried next hour
    entry.update(price=product.price, name=product.name, url=product.url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print, don't send or save")
    parser.add_argument("--only", help="run a single watch id")
    args = parser.parse_args(argv)

    watches = load_watches()
    if args.only:
        watches = [w for w in watches if w["id"] == args.only]
    state = load_state()
    for i, watch in enumerate(watches):
        if i:
            time.sleep(REQUEST_PAUSE_S)
        run_watch(watch, state, args.dry_run)

    # Forget watches that were removed from watches.yaml.
    current = {w["id"] for w in load_watches()}
    state = {k: v for k, v in state.items() if k in current}
    if not args.dry_run:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
