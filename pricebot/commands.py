"""Watch work that needs Akakçe – done by GitHub runs for the Telegram menu (cloudflare/worker.js):

* setup_watch    – a watch just added in the menu: pick the search words, guess a minimum
                   price so accessories don't fill the list
* seller_options – the sellers of a watch's cheapest products, for the menu's 🏪 screen

Pure apart from the `fetch` / `offers_for` / `probe` callbacks, so it can be tested offline.
"""

from __future__ import annotations

import statistics

from . import akakce
from .matching import compact, matches, ranked

AUTO_MIN_FACTOR = 0.6  # new watch without "min": ignore listings under 60% of the typical price

BOT_COMMANDS = [  # Telegram's "/" menu
    ("menu", "Ana menü"),
    ("ekle", "Yeni takip"),
    ("liste", "Takiplerim"),
    ("ozet", "Özet"),
    ("yardim", "Nasıl kullanılır"),
]


def default_probe(watch: dict) -> list:
    """Relevance-ordered results (real products first) used to guess a sensible min price."""
    if watch.get("url"):
        return akakce.category(watch["url"])
    return akakce.search(watch["query"], sort=1)


def setup_watch(watch: dict, fetch, probe=None) -> list:
    """Finish a watch added in the menu (in place). Returns its matching products, cheapest
    first – empty if nothing matches. Raises akakce.AkakceError if Akakçe can't be read."""
    probe = probe or default_probe
    relevant = probe(watch)  # Akakçe's relevance order: the real product first
    if watch.get("query"):
        # 1. The category of Akakçe's most relevant result: "9800x3d" -> islemci (not the
        #    gaming PCs with a 9800X3D inside), "1000w psu" -> power-supply (not cases with one).
        first = next((p for p in relevant if p.category), None)
        if first and "category" not in watch:
            watch["category"] = first.category
        # 2. Within it, prefer the words side by side ("iphone 16" – not "iPhone 17/16/15
        #    uyumlu SSD"), else all words anywhere ("chanel coco 100 ml"), else none – Akakçe
        #    calls them "1000 W Power Supply", so trust its search plus the category.
        for include in ([watch["query"]], watch["query"].split(), []):
            if not include or len(ranked(relevant, {**watch, "include": include})) >= 2:
                break
        if include:
            watch["include"] = include
        else:
            watch.pop("include", None)
    if "min_price" not in watch:
        # Cases, screen protectors and "uyumlu" accessories are far cheaper than the real thing
        # and fill a cheapest-first page. Akakçe's relevance order lists the real products
        # first, so 60% of their median price is a good lower limit.
        sample = [p for p in relevant if matches(p, watch)][:12]
        if len(sample) >= 3:
            auto_min = int(statistics.median(p.price for p in sample) * AUTO_MIN_FACTOR / 100) * 100
            if auto_min > 0:
                watch["min_price"] = auto_min
    return ranked(fetch(watch), watch)


def seller_options(matching: list, offers_for, top: int = 5) -> list[list]:
    """[[seller, cheapest offer], …] over the `top` cheapest products, cheapest first."""
    best: dict[str, float] = {}
    for p in matching[:top]:
        try:
            for o in offers_for(p):
                best[o["seller"]] = min(best.get(o["seller"], o["price"]), o["price"])
        except akakce.AkakceError:
            continue
    return [[name, price] for name, price in sorted(best.items(), key=lambda kv: kv[1])]


def is_chosen(seller: str, watch: dict) -> bool:
    return any(compact(c) and compact(c) in compact(seller) for c in watch.get("sellers") or [])
