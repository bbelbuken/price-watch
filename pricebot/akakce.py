"""Read search results and seller offers from Akakçe.

Akakçe renders its pages with Astro: the data behind the product list sits in
the `props` attribute of an <astro-island> tag as HTML-escaped JSON, where every
value is wrapped as [type, value] (0 = plain value/object, 1 = array).
Reading that JSON is far more stable than scraping CSS class names like `pw_v8`.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

BASE = "https://www.akakce.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

_PROPS_RE = re.compile(r'<astro-island\b[^>]*?\sprops="([^"]*)"')


class AkakceError(Exception):
    """The page could not be fetched or no longer has the expected structure."""


@dataclass
class Product:
    code: int
    name: str
    price: float
    url: str
    offer_count: int


@dataclass
class Offer:
    seller: str
    price: float
    ship_price: float


def _decode(value):
    """Unwrap Astro's [type, value] serialization recursively."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        kind, inner = value
        if kind == 1 and isinstance(inner, list):
            return [_decode(v) for v in inner]
        if isinstance(inner, dict):
            return {k: _decode(v) for k, v in inner.items()}
        return inner
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    return value


def _islands(page: str):
    for raw in _PROPS_RE.findall(page):
        try:
            yield _decode(json.loads(html.unescape(raw)))
        except (ValueError, TypeError):
            continue


def _product_lists(node):
    """Yield every `products` list in the tree whose items look like products.

    A search lands either on the search page (searchData.productList.products) or,
    for category-like queries such as "rx 9070 ekran kartı", gets redirected to a
    category page (initialData.components[type=productList].data[].products).
    Looking for the shape instead of the path handles both.
    """
    if isinstance(node, dict):
        items = node.get("products")
        if isinstance(items, list) and any(
            isinstance(p, dict) and {"code", "price", "name"} <= p.keys() for p in items
        ):
            yield items
        for value in node.values():
            yield from _product_lists(value)
    elif isinstance(node, list):
        for value in node:
            yield from _product_lists(value)


def parse_search(page: str) -> list[Product]:
    found: dict[int, Product] = {}
    for props in _islands(page):
        for items in _product_lists(props):
            for p in items:
                if not isinstance(p, dict) or p.get("price") is None or not p.get("name"):
                    continue
                code = int(p.get("code") or 0)
                found.setdefault(
                    code,
                    Product(
                        code=code,
                        name=str(p["name"]),
                        price=float(p["price"]),
                        url=BASE + str(p.get("url") or ""),
                        offer_count=int(p.get("countOfPrices") or 0),
                    ),
                )
    if not found:
        raise AkakceError("no products found in page (layout changed, blocked or no results)")
    return list(found.values())


def parse_offers(page: str) -> list[Offer]:
    """Seller offers on a product page, cheapest first as Akakçe lists them."""
    for props in _islands(page):
        offers = props.get("initialPgList")
        if isinstance(offers, list) and offers:
            return [
                Offer(
                    seller=str(o.get("vdName") or "?"),
                    price=float(o.get("price") or 0),
                    ship_price=float(o.get("shipPrice") or 0),
                )
                for o in offers
                if isinstance(o, dict) and o.get("price") is not None
            ]
    return []


def _get(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as exc:
        raise AkakceError(f"request failed: {exc}") from exc
    if resp.status_code != 200:
        raise AkakceError(f"HTTP {resp.status_code}")
    resp.encoding = "utf-8"
    return resp.text


def search(query: str) -> list[Product]:
    return parse_search(_get(f"{BASE}/arama/?q={quote_plus(query)}"))


def offers(product_url: str) -> list[Offer]:
    return parse_offers(_get(product_url))
