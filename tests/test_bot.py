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

def test_drop_message_dropped_products_then_cheapest_5():
    gpu = main.WatchResult(W, top=[P(1, 57000), P(2, 61000)], drops=[main.Drop(P(1, 57000), 61900)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d", "target": None}, top=[P(5, 20000)])
    text, reported = main.build_message([gpu, cpu], None, {"x": "n11"})
    dropped = "*57.000₺  RX 9070 XT* ~61.900₺~ %8 🔥 ⬇️"  # bold, old price struck, emojis at the end
    assert text.splitlines() == [
        "9070 xt: RX 9070 XT 57.000₺ oldu, %8 düştü ⬇️",  # the push title says what happened
        "",
        "▸ *9070 xt* · hedef 58.000₺ 🎯",
        "*Düşen ürünler* 📉",
        dropped,
        "──────────",
        "*En ucuz 2 ürün* 🏆",  # only 2 matched: the heading says what's really listed
        dropped,  # marked the same way in the top list
        "61.000₺  RX 9070 XT",
        "En ucuz: n11 🏪",
        "🔗 https://www.akakce.com/arama/?q=9070+xt&s=2",
    ]
    assert "9800x3d" not in text  # no drop there: not shown
    assert [(r.watch["id"], [p.code for p in ps]) for r, ps in reported] == [("x", [1, 2])]


def test_summary_has_no_drop_section():
    gpu = main.WatchResult(W, top=[P(1, 57000), P(2, 61000)], drops=[main.Drop(P(1, 57000), 61900)])
    text, _ = main.build_message([gpu], "2026-11-21 21:00", {})
    assert text.startswith("Akşam özeti · 21:00 🌙")
    assert "Düşen ürünler" not in text and "*57.000₺  RX 9070 XT* ~61.900₺~ %8 🔥 ⬇️" in text


@pytest.mark.parametrize("drop, title", [
    (main.Drop(P(1, 57000), 61900), "9070 xt: RX 9070 XT 57.000₺ oldu, %8 düştü ⬇️"),
    (main.Drop(P(1, 57000), 61900, entered=True), "9070 xt: RX 9070 XT ilk 5'e girdi – 57.000₺ 🆕"),
    (main.Drop(P(1, 57000), None, entered=True), "9070 xt: RX 9070 XT ilk 5'e girdi – 57.000₺ 🆕"),
    (main.Drop(P(1, 57000), 61900, first=True), "9070 xt: yeni en ucuz 57.000₺ – RX 9070 XT 🥇"),
])
def test_alert_title_says_what_happened(drop, title):
    res = main.WatchResult(W, top=[drop.product], drops=[drop])
    assert main.build_message([res], None, {})[0].splitlines()[0] == title


def test_alert_title_picks_the_biggest_change_and_counts_the_rest():
    gpu = main.WatchResult(W, top=[P(1, 57000)], drops=[main.Drop(P(1, 57000), 61900)])
    seller = Product(5, "iPhone 17 256 GB", 84000, "u", 1, "Amazon Türkiye")
    phone = main.WatchResult({"id": "gf-1#satici", "query": "iphone 17", "name": "iphone 17 · Amazon Türkiye"},
                             top=[seller], drops=[main.Drop(seller, 85000, first=True)])
    title = main.build_message([gpu, phone], None, {})[0].splitlines()[0]
    assert title == "iphone 17 · Amazon Türkiye: yeni en ucuz 84.000₺ – 256 GB (+1) 🥇"


def test_heading_counts_the_products_really_listed():
    """Top 10 set, but only 5 products (e.g. a seller list): the heading says 5."""
    products = [P(i, 50000 + i * 1000) for i in range(1, 6)]
    w10 = {**W, "top_n": 10}
    entry = {}
    main.evaluate(w10, products, entry, 1)
    res = main.evaluate(w10, products[:4] + [P(5, 50000)], entry, 1)  # 5th drops
    assert [d.product.code for d in res.drops] == [5]
    text = main.build_message([res], None, {})[0]
    assert "*En ucuz 5 ürün* 🏆" in text and "En ucuz 10" not in text


@pytest.mark.parametrize("n, text", [(5, "5'e"), (10, "10'a"), (15, "15'e"), (20, "20'ye")])
def test_turkish_dative_for_top_n(n, text):
    assert main.to_n(n) == text


def test_top_n_decides_the_list_and_what_can_alert():
    products = [P(i, 50000 + i * 1000) for i in range(1, 13)]
    w10 = {**W, "top_n": 10}
    entry = {}
    main.evaluate(w10, products, entry, 1)
    cheaper = products[:11] + [P(12, 40000)]  # 12th place drops into 1st
    res = main.evaluate(w10, sorted(cheaper, key=lambda p: p.price), entry, 1)
    assert len(res.top) == 10 and [d.product.code for d in res.drops] == [12]
    assert res.drops[0].first and res.drops[0].entered
    text = main.build_message([res], None, {})[0]
    assert "*En ucuz 10 ürün* 🏆" in text and len([l for l in text.splitlines() if "₺  " in l]) == 11
    # with the default 5 the same drop outside the cheapest 5 says nothing
    entry5 = {}
    main.evaluate(W, products, entry5, 1)
    moved = [P(i, 50000 + i * 1000) for i in range(1, 12)] + [P(12, 55500)]  # 6th now – still outside 5
    assert main.evaluate(W, sorted(moved, key=lambda p: p.price), entry5, 1).drops == []


def test_entered_title_uses_top_n():
    d = main.Drop(P(1, 57000), 61900, entered=True)
    res = main.WatchResult({**W, "top_n": 20}, top=[d.product], drops=[d])
    assert "ilk 20'ye girdi" in main.build_message([res], None, {})[0].splitlines()[0]


def test_drop_marks_on_product_lines():
    w = {"query": "9070 xt"}
    assert main.product_line(P(1, 57000), w, main.Drop(P(1, 57000), 61900, first=True)).endswith("%8 ⬇️ 🥇")
    assert main.product_line(P(1, 57000), w, main.Drop(P(1, 57000), None)) == "57.000₺  RX 9070 XT 🆕"
    assert main.product_line(P(1, 57000), w, main.Drop(P(1, 57000), 61900, entered=True)).endswith("⬇️ 🆕")


def test_three_watches_dropping_come_in_one_message():
    gpu = main.WatchResult(W, top=[P(1, 57000)], drops=[main.Drop(P(1, 57000), 61900)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d"}, top=[P(5, 19500)],
                           drops=[main.Drop(P(5, 19500), 20100)])
    psu = main.WatchResult({**W, "id": "psu", "query": "1000w psu"}, top=[P(7, 6000)],
                           drops=[main.Drop(P(7, 6000), None)])
    text, reported = main.build_message([gpu, cpu, psu], None, {})
    assert text.splitlines()[0] == "1000w psu: 9070 XT ilk 5'e girdi – 6.000₺ (+2) 🆕"
    assert all(f"*{q}*" in text for q in ("9070 xt", "9800x3d", "1000w psu"))
    assert "6.000₺  9070 XT 🔥 🆕" in text and len(reported) == 3


def test_summary_has_every_watch_in_one_message():
    gpu = main.WatchResult(W, top=[P(1, 57000), P(2, 61000)])
    cpu = main.WatchResult({**W, "id": "cpu", "query": "9800x3d"}, top=[P(5, 20000)])
    bad = main.WatchResult({**W, "id": "z", "query": "parfum"}, error="HTTP 403")
    text, reported = main.build_message([gpu, cpu, bad], "2026-11-21 09:00", {})
    assert text.startswith("Sabah özeti · 09:00 ☀️")
    assert "*9070 xt*" in text and "*9800x3d*" in text and "*parfum*: Akakçe okunamadı (HTTP 403) ⚠️" in text
    assert len(reported) == 2


def test_quiet_run_sends_nothing():
    res = main.WatchResult(W, top=[P(1, 60000)])
    assert main.build_message([res], None, {}) == (None, [])


def test_telegram_html_conversion():
    from pricebot.notify import to_telegram_html
    assert to_telegram_html("*9070 xt* · <OC> & co\n1. 57.000 TL") == (
        "<b>9070 xt</b> · &lt;OC&gt; &amp; co\n1. 57.000 TL")
    assert to_telegram_html("*57.000₺  Gigabyte* ~61.900₺~ %8 ⬇️") == (
        "<b>57.000₺  Gigabyte</b> <s>61.900₺</s> %8 ⬇️")


def test_ntfy_has_no_strikethrough():
    from pricebot.notify import ntfy_payloads
    [p] = ntfy_payloads("t", "📉 Fiyat düştü!\n\n*57.000₺  Gigabyte* ~61.900₺~ %8 ⬇️")
    assert p["message"] == "57.000₺  Gigabyte (önce 61.900₺) %8 ⬇️"


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
    [p] = ntfy_payloads("t", "9070 xt: Gigabyte 57.000₺ oldu ⬇️\n\n*9070 xt* · hedef\n1. 57.000 TL")
    assert p == {"topic": "t", "title": "9070 xt: Gigabyte 57.000₺ oldu ⬇️",
                 "message": "9070 xt · hedef\n1. 57.000 TL", "priority": 4}
    [s] = ntfy_payloads("t", "☀️ Sabah özeti · 09:00\n\n*a*\n1. x")
    assert s["priority"] == 3


def test_ntfy_links_become_buttons():
    from pricebot.notify import ntfy_payloads
    text = ("🌙 Akşam özeti\n\n▸ *9070 xt* · 🎯 58.000₺\n61.900₺  Gigabyte Gaming OC\n🔗 https://a\n\n"
            "▸ *RX 9070*\n49.858₺  PowerColor Reaper\n🔗 https://b")
    [p] = ntfy_payloads("t", text)
    assert p["actions"] == [{"action": "view", "label": "9070 xt", "url": "https://a"},
                            {"action": "view", "label": "RX 9070", "url": "https://b"}]
    assert "🔗" not in p["message"] and "61.900₺  Gigabyte Gaming OC" in p["message"]


def test_ntfy_long_message_is_numbered():
    from pricebot.notify import ntfy_payloads
    text = "🌙 Akşam özeti\n\n" + "\n\n".join(f"*w{i}*\n" + "x" * 900 for i in range(4))
    parts = ntfy_payloads("t", text)
    assert len(parts) > 1
    assert [p["title"] for p in parts][-1] == f"🌙 Akşam özeti ({len(parts)}/{len(parts)})"
    assert all(len(p["message"]) <= 1800 for p in parts)


@pytest.mark.parametrize("name, query, expected", [
    ("Gigabyte RX 9070 XT Gaming OC GV-R9070XTGAMING OC-16GD 256 Bit GDDR6 16 GB Ekran Kartı",
     "9070 xt", "Gigabyte Gaming OC"),
    ("Sapphire RX 9070 XT Pulse 11348-03-20G 256 Bit GDDR6 16 GB Ekran Kartı", "9070 xt", "Sapphire Pulse"),
    ("Asus RX 9070 XT Prime OC Edition PRIME-RX9070XT-O16G 256 Bit GDDR6 16 GB Ekran Kartı",
     "9070 xt", "Asus Prime OC"),
    ("PowerColor RX 9070 XT Reaper 16G-A 256 Bit GDDR6 16 GB Ekran Kartı", "9070 xt", "PowerColor Reaper 16G-A"),
    ("Chanel Coco Mademoiselle EDP 100 ml Kadın Parfüm", "coco mademoiselle", "Chanel EDP 100 ml Kadın Parfüm"),
    ("iPhone 16 Plus 256 GB Siyah", "iphone 16", "Plus 256 GB Siyah"),  # phones keep the capacity
    ("Antec CSK1000 Pro Gen5.1 1000 W Power Supply", None, "Antec CSK1000 Pro Gen5.1 1000…"),
])
def test_short_name(name, query, expected):
    assert main.short_name(name, {"query": query} if query else {}) == expected


def test_lira_and_seller():
    assert main.lira(63479.08) == "63.479₺"
    assert main.lira(49857.6) == "49.858₺"
    assert main.seller_name("Maksimumbilgisayar.com") == "Maksimumbilgisayar"
    assert main.seller_name("incehesap.com.tr") == "incehesap"


def test_tl_format():
    assert main.tl(61900) == "61.900 TL"
    assert main.tl(1983.65) == "1.983,65 TL"


def test_watches_file_is_valid():
    settings, watches = main.load_config()
    assert watches and settings["summary_times"]
