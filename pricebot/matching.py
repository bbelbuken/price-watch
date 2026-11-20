"""Decide which search results really are the product a watch is looking for."""

from __future__ import annotations

import re

from .akakce import Product

_TR = str.maketrans({"I": "ı", "İ": "i"})


def compact(text: str) -> str:
    """Lowercase (Turkish-aware), drop spaces/punctuation: 'RX 9070-XT' -> 'rx9070xt'."""
    text = text.translate(_TR).lower().replace("ı", "i")
    return re.sub(r"[\W_]+", "", text)


def matches(product: Product, watch: dict) -> bool:
    name = compact(product.name)
    if not all(compact(term) in name for term in watch.get("include") or []):
        return False
    if any(compact(term) in name for term in watch.get("exclude") or []):
        return False
    if product.price < float(watch.get("min_price") or 0):
        return False
    max_price = watch.get("max_price")
    if max_price is not None and product.price > float(max_price):
        return False
    return True


def ranked(products: list[Product], watch: dict) -> list[Product]:
    """All matching products, cheapest first."""
    return sorted((p for p in products if matches(p, watch)), key=lambda p: p.price)


def cheapest(products: list[Product], watch: dict) -> Product | None:
    candidates = ranked(products, watch)
    return candidates[0] if candidates else None
