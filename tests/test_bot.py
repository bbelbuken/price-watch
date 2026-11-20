import html
import json
from datetime import datetime, timezone

import pytest

from pricebot import akakce, main
from pricebot.akakce import Product
from pricebot.matching import cheapest, compact, ranked


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

PRODUCT_PAGE = island(
    {
        "initialPgList": [
            {"vdName": "Maksimumbilgisayar.com", "price": 61900, "shipPrice": 0},
            {"vdName": "n11", "price": 65998.96, "shipPrice": 0},
        ]
    }
)


def P(code, price, name="RX 9070 XT"):
    return Product(code, name, price, f"https://x/{code}", 1)


# ---------------------------------------------------------------- parsing

def test_parse_search():
    products = akakce.parse_search(SEARCH_PAGE)
    assert [p.code for p in products] == [1, 2, 3]
    assert products[1].price == 61900
    assert products[1].url == "https://www.akakce.com/ekran-karti/b,2.html"


def test_parse_category_page():
    assert [(p.code, p.price) for p in akakce.parse_search(CATEGORY_PAGE)] == [(9, 49857.6)]


def test_parse_search_layout_change_raises():
    with pytest.raises(akakce.AkakceError):
        akakce.parse_search("<html>Güvenlik</html>")


def test_parse_offers():
    offers = akakce.parse_offers(PRODUCT_PAGE)
    assert offers[0].seller == "Maksimumbilgisayar.com" and offers[0].price == 61900


# ---------------------------------------------------------------- matching

def test_compact_turkish():
    assert compact("RX 9070-XT") == "rx9070xt"
    assert compact("EKRAN KARTI") == "ekrankarti"


def test_xt_vs_non_xt():
    products = akakce.parse_search(SEARCH_PAGE)
    assert [p.code for p in ranked(products, {"include": ["9070 xt"]})] == [2, 1]
    assert cheapest(products, {"include": ["9070"], "exclude": ["9070 xt"]}).code == 3


def test_min_price_filters_fakes():
    products = [P(1, 1983.65, "Chanel Coco EDP"), P(2, 6500, "Chanel Coco EDP")]
    assert cheapest(products, {"include": ["coco"], "min_price": 4000}).code == 2


# ---------------------------------------------------------------- drop logic

W = {"id": "x", "owner": "me", "query": "9070 xt", "target": 58000}


def test_new_watch_has_no_drops():
    entry = {}
    res = main.evaluate(W, [P(1, 60000)], entry, 1, today="2026-11-21")
    assert res.is_new and not res.drops
    assert entry["items"]["1"] == {"price": 60000, "ref": 60000, "seen": "2026-11-21"}


def test_small_jitter_ignored_but_creeping_drop_adds_up():
    entry = {}
    main.evaluate(W, [P(1, 60000)], entry, 1)
    assert not main.evaluate(W, [P(1, 59700)], entry, 1).drops  # -0.5%
    res = main.evaluate(W, [P(1, 59350)], entry, 1)  # -1.08% vs the 60000 we told
    assert [d.before for d in res.drops] == [60000]


def test_ref_moves_down_only_when_reported():
    entry = {}
    main.evaluate(W, [P(1, 60000)], entry, 1)
    res = main.evaluate(W, [P(1, 58000)], entry, 1)
    assert res.drops
    main.mark_reported(entry, [d.product for d in res.drops])
    assert not main.evaluate(W, [P(1, 58000)], entry, 1).drops


def test_price_rise_then_fall_is_a_drop():
    entry = {}
    main.evaluate(W, [P(1, 60000)], entry, 1)
    main.evaluate(W, [P(1, 66000)], entry, 1)
    res = main.evaluate(W, [P(1, 63000)], entry, 1)
    assert [d.before for d in res.drops] == [66000]


def test_unseen_product_entering_top5_is_reported_even_if_not_cheapest():
    entry = {}
    main.evaluate(W, [P(1, 50000), P(2, 51000), P(3, 52000)], entry, 1)
    # e.g. a Nitro+ we never saw shows up at 51.500 -> 3rd place
    res = main.evaluate(W, [P(1, 50000), P(2, 51000), P(9, 51500), P(3, 52000)], entry, 1)
    assert [(d.product.code, d.before) for d in res.drops] == [(9, None)]
    assert main.evaluate(W, [P(1, 50000), P(9, 51500)], entry, 1).drops == []  # only once


def test_unseen_product_outside_top5_is_ignored():
    entry = {}
    products = [P(i, 50000 + i * 1000) for i in range(1, 6)]
    main.evaluate(W, products, entry, 1)
    res = main.evaluate(W, products + [P(99, 99000)], entry, 1)
    assert res.drops == []


def test_product_that_left_the_page_is_remembered_for_7_days():
    entry = {}
    main.evaluate(W, [P(1, 50000), P(2, 51000)], entry, 1, today="2026-11-01")
    main.evaluate(W, [P(1, 50000)], entry, 1, today="2026-11-03")  # 2 dropped off the page
    back = main.evaluate(W, [P(1, 50000), P(2, 51000)], entry, 1, today="2026-11-05")
    assert back.drops == []  # not reported as new
    main.evaluate(W, [P(1, 50000)], entry, 1, today="2026-11-20")
    assert "2" not in entry["items"]  # forgotten after 7 days unseen


@pytest.mark.parametrize("given, expected", [
    ("https://www.akakce.com/power-supply/1000-w-power-supply.html",
     "https://www.akakce.com/power-supply/1000-w-power-supply,1,2.html"),
    ("https://www.akakce.com/power-supply/1000-w-power-supply,3.html?x=1#top",
     "https://www.akakce.com/power-supply/1000-w-power-supply,1,2.html"),
    ("https://www.akakce.com/power-supply/1000-w-power-supply,2,5.html",
     "https://www.akakce.com/power-supply/1000-w-power-supply,1,2.html"),
    ("http://akakce.com/power-supply.html",
     "https://www.akakce.com/power-supply,1,2.html"),
    ("https://www.akakce.com/power-supply.html/1234,5678",
     "https://www.akakce.com/power-supply,1,2.html/1234,5678"),
])
def test_category_url_sorted_cheapest_first(given, expected):
    assert akakce.category_url(given) == expected


@pytest.mark.parametrize("bad", [
    "https://www.akakce.com/ekran-karti/en-ucuz-asus-rx-9070-xt-fiyati,965149486.html",
    "https://www.amazon.com.tr/s?k=9070",
])
def test_category_url_rejects_other_links(bad):
    with pytest.raises(ValueError):
        akakce.category_url(bad)


def test_label():
    assert main.label({"query": "9070 xt"}) == "9070 xt"
    assert main.label({"url": "https://www.akakce.com/power-supply/1000-w-power-supply.html"}) == "1000 w power supply"
    assert main.label({"name": "PSU", "url": "https://x/y.html"}) == "PSU"


def test_config_needs_exactly_one_of_query_or_url(tmp_path):
    f = tmp_path / "w.yaml"
    f.write_text("watches:\n  - {id: a, owner: me, query: x, url: 'https://www.akakce.com/a.html'}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        main.load_config(f)


def test_search_url_sorts_by_price_within_range():
    assert akakce.search_url("1000w psu", 4000, 9000) == (
        "https://www.akakce.com/arama/?q=1000w+psu&s=2&p1=4000&p2=9000")
    assert akakce.search_url("9070 xt") == "https://www.akakce.com/arama/?q=9070+xt&s=2"


def test_only_top5_are_considered():
    entry = {}
    products = [P(i, 50000 + i * 1000) for i in range(1, 8)]
    main.evaluate(W, products, entry, 1)
    cheaper7 = products[:6] + [P(7, 40000)]
    res = main.evaluate(W, sorted(cheaper7, key=lambda p: p.price), entry, 1)
    assert [d.product.code for d in res.drops] == [7]  # it moved into the top 5


# ---------------------------------------------------------------- summaries

def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_summary_slots_turkey_time():
    times = ["09:00", "21:00"]
    # 06:30 UTC = 09:30 TR -> morning slot due
    assert main.due_summary_slot(utc("2026-11-21 06:30"), times, None) == "2026-11-21 09:00"
    assert main.due_summary_slot(utc("2026-11-21 06:40"), times, "2026-11-21 09:00") is None
    # 20:00 UTC = 23:00 TR -> evening
    assert main.due_summary_slot(utc("2026-11-21 20:00"), times, "2026-11-21 09:00") == "2026-11-21 21:00"
    # 03:00 UTC = 06:00 TR -> yesterday evening already sent -> nothing
    assert main.due_summary_slot(utc("2026-11-22 03:00"), times, "2026-11-21 21:00") is None


# ---------------------------------------------------------------- messages

def test_drop_message_lists_only_dropped_items():
    gpu = main.WatchResult(W, top=[P(1, 57000), P(2, 61000)], drops=[main.Drop(P(1, 57000), 61900)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d", "target": None}, top=[P(5, 20000)])
    text, reported = main.build_message([gpu, cpu], None, {"x": "n11"})
    assert text.startswith("📉 Fiyat düştü!")
    assert "🔥57.000 TL ⬇️ %8 (61.900 TL)" in text and "🏪 n11" in text
    assert "61.000" not in text and "9800x3d" not in text
    assert "Daha fazla: https://www.akakce.com/arama/?q=9070+xt&s=2" in text
    assert [(r.watch["id"], [p.code for p in ps]) for r, ps in reported] == [("x", [1])]


def test_three_watches_dropping_come_in_one_message():
    gpu = main.WatchResult(W, top=[P(1, 57000)], drops=[main.Drop(P(1, 57000), 61900)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d"}, top=[P(5, 19500)],
                           drops=[main.Drop(P(5, 19500), 20100)])
    psu = main.WatchResult({**W, "id": "psu", "query": "1000w psu"}, top=[P(7, 6000)],
                           drops=[main.Drop(P(7, 6000), None)])
    text, reported = main.build_message([gpu, cpu, psu], None, {})
    assert text.startswith("📉 Fiyat düştü!")
    assert all(f"*{q}*" in text for q in ("9070 xt", "9800x3d", "1000w psu"))
    assert "🆕 ilk 5'e girdi" in text and len(reported) == 3


def test_summary_has_every_watch_in_one_message():
    gpu = main.WatchResult(W, top=[P(1, 57000), P(2, 61000)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d"}, top=[P(5, 20000)])
    bad = main.WatchResult({**W, "id": "z", "query": "parfum"}, error="HTTP 403")
    text, reported = main.build_message([gpu, cpu, bad], "2026-11-21 09:00", {})
    assert text.startswith("☀️ Sabah özeti · 09:00")
    assert "*9070 xt*" in text and "*9800x3d*" in text and "⚠️ *parfum*" in text
    assert len(reported) == 2


def test_quiet_run_sends_nothing():
    res = main.WatchResult(W, top=[P(1, 60000)])
    assert main.build_message([res], None, {}) == (None, [])


def test_telegram_html_conversion():
    from pricebot.notify import to_telegram_html
    assert to_telegram_html("*9070 xt* · <OC> & co\n1. 57.000 TL") == (
        "<b>9070 xt</b> · &lt;OC&gt; &amp; co\n1. 57.000 TL")


def test_split_keeps_watch_blocks_together():
    from pricebot.notify import split
    text = "\n\n".join(["title"] + [f"*w{i}*\n" + "x" * 90 for i in range(5)])
    parts = split(text, 250)
    assert all(len(p) <= 250 for p in parts) and len(parts) > 1
    assert "\n\n".join(parts) == text


def test_channel_order_telegram_ntfy_whatsapp(monkeypatch):
    from pricebot import notify
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID_ME", "NTFY_TOPIC_ME", "PHONE_ME", "CALLMEBOT_APIKEY_ME"):
        monkeypatch.delenv(k, raising=False)
    assert notify.channel("me") is None
    monkeypatch.setenv("PHONE_ME", "+90")
    monkeypatch.setenv("CALLMEBOT_APIKEY_ME", "1")
    assert notify.channel("me") == "whatsapp"
    monkeypatch.setenv("NTFY_TOPIC_ME", "secret-topic")
    assert notify.channel("me") == "ntfy"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ME", "42")
    assert notify.channel("me") == "telegram"


def test_ntfy_payloads_title_body_priority():
    from pricebot.notify import ntfy_payloads
    [p] = ntfy_payloads("t", "📉 Fiyat düştü!\n\n*9070 xt* · hedef\n1. 57.000 TL")
    assert p == {"topic": "t", "title": "📉 Fiyat düştü!",
                 "message": "9070 xt · hedef\n1. 57.000 TL", "priority": 4}
    [s] = ntfy_payloads("t", "☀️ Sabah özeti · 09:00\n\n*a*\n1. x")
    assert s["priority"] == 3


def test_ntfy_long_message_is_numbered():
    from pricebot.notify import ntfy_payloads
    text = "🌙 Akşam özeti\n\n" + "\n\n".join(f"*w{i}*\n" + "x" * 900 for i in range(4))
    parts = ntfy_payloads("t", text)
    assert len(parts) > 1
    assert [p["title"] for p in parts][-1] == f"🌙 Akşam özeti ({len(parts)}/{len(parts)})"
    assert all(len(p["message"]) <= 1800 for p in parts)


def test_tl_format():
    assert main.tl(61900) == "61.900 TL"
    assert main.tl(1983.65) == "1.983,65 TL"


def test_watches_file_is_valid():
    settings, watches = main.load_config()
    assert watches and settings["summary_times"]
