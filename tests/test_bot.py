import html
import json

import pytest

from pricebot import akakce, main
from pricebot.akakce import Product
from pricebot.matching import cheapest, compact


def wrap(value):
    """Encode a Python value the way Akakçe's Astro props do."""
    if isinstance(value, list):
        return [1, [wrap(v) for v in value]]
    if isinstance(value, dict):
        return [0, {k: wrap(v) for k, v in value.items()}]
    return [0, value]


def island(props: dict) -> str:
    encoded = html.escape(json.dumps({k: wrap(v) for k, v in props.items()}), quote=True)
    return f'<astro-island uid="x" component-url="/a.js" props="{encoded}"></astro-island>'


SEARCH_PAGE = "<html><body>" + island(
    {
        "searchData": {
            "productList": {
                "countOfProducts": 3,
                "products": [
                    {"code": 1, "price": 64798, "name": "Asus RX 9070 XT Prime OC Ekran Kartı",
                     "url": "/ekran-karti/a,1.html", "countOfPrices": 169},
                    {"code": 2, "price": 61900, "name": "Gigabyte RX 9070 XT Gaming OC",
                     "url": "/ekran-karti/b,2.html", "countOfPrices": 22},
                    {"code": 3, "price": 52000, "name": "Sapphire RX 9070 Pulse 16 GB",
                     "url": "/ekran-karti/c,3.html", "countOfPrices": 7},
                ],
            }
        },
        "pageParams": {},
    }
) + "</body></html>"

PRODUCT_PAGE = island(
    {
        "initialPgList": [
            {"vdName": "Maksimumbilgisayar.com", "price": 61900, "shipPrice": 0},
            {"vdName": "n11", "price": 65998.96, "shipPrice": 0},
        ]
    }
)


def test_parse_search():
    products = akakce.parse_search(SEARCH_PAGE)
    assert [p.code for p in products] == [1, 2, 3]
    assert products[1].price == 61900
    assert products[1].url == "https://www.akakce.com/ekran-karti/b,2.html"


CATEGORY_PAGE = island(
    {
        "initialData": {
            "components": [
                {"type": "filters", "data": []},
                {"type": "productList", "data": [{
                    "countOfProducts": 1,
                    "products": [{"code": 9, "name": "PowerColor RX 9070 Reaper", "price": 49857.6,
                                  "url": "/ekran-karti/d,9.html", "countOfPrices": 2}],
                }]},
            ]
        }
    }
)


def test_parse_category_page():
    products = akakce.parse_search(CATEGORY_PAGE)
    assert [(p.code, p.price) for p in products] == [(9, 49857.6)]


def test_parse_search_layout_change_raises():
    with pytest.raises(akakce.AkakceError):
        akakce.parse_search("<html>Güvenlik</html>")


def test_parse_offers():
    offers = akakce.parse_offers(PRODUCT_PAGE)
    assert offers[0].seller == "Maksimumbilgisayar.com"
    assert offers[0].price == 61900


def test_compact_turkish():
    assert compact("RX 9070-XT") == "rx9070xt"
    assert compact("EKRAN KARTI") == "ekrankarti"


def test_cheapest_xt_vs_non_xt():
    products = akakce.parse_search(SEARCH_PAGE)
    xt = {"include": ["9070 xt"]}
    non_xt = {"include": ["9070"], "exclude": ["9070 xt"]}
    assert cheapest(products, xt).code == 2
    assert cheapest(products, non_xt).code == 3


def test_cheapest_min_price_filters_fakes():
    products = [Product(1, "Chanel Coco EDP 100 ml", 1983.65, "u", 1),
                Product(2, "Chanel Coco EDP 100 ml", 6500, "u", 9)]
    assert cheapest(products, {"include": ["coco"], "min_price": 4000}).code == 2


def test_decide():
    p = Product(1, "x", 61900, "u", 1)
    assert main.decide({}, p, {}) == "first"
    assert main.decide({}, p, {"price": 61900}) is None
    assert main.decide({}, p, {"price": 62000}) is None  # 0.16% jitter
    assert main.decide({}, p, {"price": 64000}) == "drop"
    assert main.decide({"min_drop_pct": 0}, p, {"price": 62000}) == "drop"


def test_message_target_and_drop():
    p = Product(1, "Gigabyte RX 9070 XT", 57500, "https://x", 1)
    text = main.message({"query": "9070 xt", "target": 58000}, p, "drop", 61900)
    assert text.startswith("🔥")
    assert "57.500 TL" in text and "61.900 TL" in text and "-4.400 TL" in text


def test_tl_format():
    assert main.tl(61900) == "61.900 TL"
    assert main.tl(1983.65) == "1.983,65 TL"


def test_watches_file_is_valid():
    watches = main.load_watches()
    assert watches and all(w["owner"] for w in watches)
