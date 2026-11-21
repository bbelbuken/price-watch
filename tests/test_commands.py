"""GitHub side of the Telegram menu: jobs from the Worker, the watch list in the Worker,
and "send the list once after a change". The menu itself is tested in tests/worker.test.mjs."""

import pytest

from pricebot import akakce, commands, main, notify
from pricebot.akakce import Product


def P(code, price, name="iPhone 17 256 GB"):
    return Product(code, name, price, f"https://x/{code}", 5)


PHONES = [P(1, 81997), P(2, 84999, "iPhone 17 256 GB Siyah"), P(3, 89999, "iPhone 17 512 GB"),
          P(4, 40445, "Transcend SSD iPhone 17/16 uyumlu"), P(5, 900, "iPhone 17 Kılıf")]
RELEVANCE = [P(1, 81997), P(2, 84999, "iPhone 17 256 GB Siyah"), P(3, 89999, "iPhone 17 512 GB"),
             P(6, 98999, "iPhone 17 512 GB Beyaz")]
OFFERS = {
    1: [{"seller": "Pttavm", "price": 81997}, {"seller": "Amazon Türkiye", "price": 86849},
        {"seller": "Hepsiburada", "price": 87210}],
    2: [{"seller": "Amazon Türkiye", "price": 84999}, {"seller": "n11", "price": 85500}],
    3: [{"seller": "Pttavm", "price": 89999}],
}
SETTINGS = {"summary_times": ["09:00", "21:00"], "min_drop_pct": 1}


def fake_offers(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(akakce, "offers", lambda url: [
        akakce.Offer(o["seller"], o["price"], 0) for o in OFFERS.get(int(url.rsplit("/", 1)[1]), [])])


# ---------------------------------------------------------------- new watch setup

def test_setup_picks_phrase_and_auto_min():
    w = {"id": "gf-1", "owner": "gf", "query": "iphone 17", "include": ["iphone 17"]}
    matching = commands.setup_watch(w, lambda x: PHONES, lambda x: RELEVANCE)
    assert w["include"] == ["iphone 17"]  # words side by side
    assert w["min_price"] == 52400  # 60% of the relevance median 87499 – no cases/SSDs
    assert [p.code for p in matching] == [1, 2, 3]


def C(code, price, name, category):
    return Product(code, name, price, f"https://www.akakce.com/{category}/en-ucuz-x,{code}.html", 5)


def test_setup_picks_the_category_of_the_best_match():
    """'9800x3d': the CPU comes first in Akakçe's relevance order, then gaming PCs with it inside."""
    relevance = [C(1, 22649, "AMD Ryzen 7 9800X3D İşlemci", "islemci"),
                 C(2, 22670, "AMD Ryzen 7 9800X3D Kutusuz İşlemci", "islemci"),
                 C(3, 140999, "Asus ROG G700 Ryzen 7 9800X3D 16 GB", "oyun-bilgisayari"),
                 C(4, 191999, "Asus ROG G700 Ryzen 7 9800X3D RTX5080", "oyun-bilgisayari"),
                 C(5, 94867, "Xaser Cube X270 Ryzen 7 9800X3D", "oyun-bilgisayari"),
                 Product(6, "9800X3D reklam", 1, "https://www.akakce.com", 1)]  # no link
    w = {"id": "me-1", "owner": "me", "query": "9800x3d"}
    matching = commands.setup_watch(w, lambda x: relevance, lambda x: relevance)
    assert w["category"] == "islemci" and "min_price" not in w  # only 2 CPUs: no guessing needed
    assert [p.code for p in matching] == [1, 2]


def test_setup_trusts_akakce_when_the_words_never_appear():
    """'1000w psu': Akakçe finds PSUs, but their names say '1000 W Power Supply'."""
    relevance = [C(1, 4783, "Gametech GTP-1000 1000 W Gen5 Power Supply", "power-supply"),
                 C(2, 5334, "GAMETECH 1000W 80 Plus Gold Atx 3.0", "power-supply"),
                 C(3, 4989, "Powerboost Sea View 1000W PSU'lu Kasa", "bilgisayar-kasasi"),
                 C(4, 5528, "Antec CSK1000 Pro Gen5.1 1000 W Power Supply", "power-supply")]
    w = {"id": "me-1", "owner": "me", "query": "1000w psu", "include": ["1000w psu"]}
    matching = commands.setup_watch(w, lambda x: relevance, lambda x: relevance)
    assert "include" not in w and w["category"] == "power-supply"
    assert [p.code for p in matching] == [1, 2, 4]  # the case with a PSU is gone


def test_category_from_product_link():
    assert C(1, 1, "x", "islemci").category == "islemci"
    assert Product(1, "x", 1, "https://www.akakce.com", 1).category is None
    assert Product(1, "x", 1, "", 1).category is None


def test_setup_falls_back_to_words_and_keeps_given_min():
    perfume = [P(i, 5000 + i, "Chanel Coco Mademoiselle EDP 100 ml") for i in range(5)]
    w = {"id": "gf-1", "owner": "gf", "query": "chanel coco 100 ml", "min_price": 1000}
    commands.setup_watch(w, lambda x: perfume, lambda x: perfume)
    assert w["include"] == ["chanel", "coco", "100", "ml"] and w["min_price"] == 1000


def test_seller_options_cheapest_first():
    opts = commands.seller_options([P(1, 0), P(2, 0), P(3, 0)], lambda p: OFFERS[p.code])
    assert opts[:3] == [["Pttavm", 81997], ["Amazon Türkiye", 84999], ["n11", 85500]]


# ---------------------------------------------------------------- list once after a change

def test_list_is_sent_once_after_a_change(monkeypatch):
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 81997), P(2, 84999)])
    watch = {"id": "gf-1", "owner": "gf", "query": "iphone 17"}
    state = {}
    [first] = main.check(watch, state, SETTINGS)
    assert first.is_new  # never seen -> its list goes out once
    [again] = main.check(watch, state, SETTINGS)
    assert not again.is_new  # nothing changed -> quiet
    [target] = main.check({**watch, "target": 50000}, state, SETTINGS)
    assert not target.is_new  # target only adds 🔥, the list is the same
    [changed] = main.check({**watch, "exclude": ["kılıf"]}, state, SETTINGS)
    assert changed.is_new  # menu changed which products count -> once more


def test_new_watch_shown_by_the_telegram_run_is_not_sent_again(monkeypatch):
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 81997)])
    watch = {"id": "gf-1", "owner": "gf", "query": "iphone 17"}
    [res] = main.check(watch, {}, SETTINGS, {"shown_fp": main.fingerprint(watch)})
    assert not res.is_new


def test_old_memory_without_fingerprint_is_kept(monkeypatch):
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 81997)])
    state = {"watches": {"gf-1": {"items": {"1": {"price": 90000, "ref": 90000, "seen": "2026-11-20"}}}}}
    [res] = main.check({"id": "gf-1", "owner": "gf", "query": "iphone 17"}, state, SETTINGS)
    assert not res.is_new and [d.before for d in res.drops] == [90000]


def test_seller_list_resets_with_the_watch(monkeypatch):
    fake_offers(monkeypatch)
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 81997), P(2, 84999), P(3, 89999)])
    watch = {"id": "gf-1", "owner": "gf", "query": "iphone 17", "sellers": ["amazon"]}
    general, amazon = main.check(watch, {}, SETTINGS)
    assert general.is_new and amazon.is_new
    assert [(p.price, p.seller) for p in amazon.top] == [(84999, "Amazon Türkiye"), (86849, "Amazon Türkiye")]


# ---------------------------------------------------------------- price run with the Worker

@pytest.fixture
def worker(monkeypatch):
    """A fake Worker: the watch list, metas and jobs it holds, and what the bot sends."""
    db = {"rows": [], "metas": {}, "jobs": [], "acked": [], "put": [], "deleted": [], "sent": [], "replies": []}
    monkeypatch.setenv("INBOX_URL", "https://relay")
    monkeypatch.setenv("INBOX_TOKEN", "x")
    monkeypatch.setattr(notify, "watches_fetch", lambda: [dict(r) for r in db["rows"]])
    monkeypatch.setattr(notify, "watches_import", lambda b: db["rows"].extend(
        {"id": w["id"], "owner": w["owner"], "body": w, "meta": {}} for w in b) or True)
    monkeypatch.setattr(notify, "watches_put", lambda b: db["put"].extend(b) or True)
    monkeypatch.setattr(notify, "watches_delete", lambda ids: db["deleted"].extend(ids) or True)
    monkeypatch.setattr(notify, "watches_meta", lambda m: db["metas"].update(m) or True)
    monkeypatch.setattr(notify, "jobs_fetch", lambda: list(db["jobs"]))
    monkeypatch.setattr(notify, "jobs_ack", lambda ids: db["acked"].extend(ids))
    monkeypatch.setattr(notify, "telegram_set_commands", lambda c: True)
    monkeypatch.setattr(notify, "send", lambda owner, text, dry_run=False: db["sent"].append((owner, text)) or True)
    monkeypatch.setattr(notify, "telegram_reply", lambda chat, text, buttons=None: db["replies"].append((chat, text, buttons)) or True)
    monkeypatch.setattr(main, "load_config", lambda path=None: (dict(SETTINGS), [
        {"id": "rx9070xt", "owner": "me", "query": "9070 xt"}]))
    monkeypatch.setattr(main, "save_config", lambda s, w, path=None: None)
    monkeypatch.setattr(main, "save_state", lambda s: None)
    monkeypatch.setattr(main, "best_seller", lambda p: "")
    fake_offers(monkeypatch)
    return db


def test_first_price_run_imports_watches_yaml_into_the_worker(worker, monkeypatch):
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 61900, "RX 9070 XT")])
    monkeypatch.setattr(main, "load_state", lambda: {})
    main.main([])
    assert [r["id"] for r in worker["rows"]] == ["rx9070xt"]
    assert worker["metas"]["rx9070xt"]["cheapest"] == 61900  # for the menu's list
    assert worker["metas"]["rx9070xt"]["shown_fp"]  # its list was sent once


def test_price_run_uses_the_workers_list_and_writes_seller_options(worker, monkeypatch):
    worker["rows"] = [{"id": "gf-1", "owner": "gf", "body": {"id": "gf-1", "owner": "gf", "query": "iphone 17"},
                       "meta": {}}]
    monkeypatch.setattr(main, "fetch", lambda w: [P(1, 81997), P(2, 84999)])
    monkeypatch.setattr(main, "load_state", lambda: {})
    main.main([])
    assert [o for o, _ in worker["sent"]] == ["gf"]  # not "me": watches.yaml isn't used
    assert worker["metas"]["gf-1"]["seller_options"][0] == ["Pttavm", 81997]


# ---------------------------------------------------------------- jobs (Telegram runs)

def gf_watch(**extra):
    return {"id": "gf-1", "owner": "gf", "body": {"id": "gf-1", "owner": "gf", "query": "iphone 17",
                                                    "include": ["iphone 17"], **extra}, "meta": {}}


def test_setup_job_finishes_the_watch_and_shows_it_once(worker, monkeypatch):
    worker["rows"] = [gf_watch()]
    worker["jobs"] = [{"id": 1, "type": "setup", "owner": "gf", "chat": "222", "watch": "gf-1"}]
    monkeypatch.setattr(main, "fetch", lambda w: PHONES)
    monkeypatch.setattr(commands, "default_probe", lambda w: RELEVANCE)
    monkeypatch.setattr(main, "load_state", lambda: pytest.fail("Telegram runs must not load state.json"))
    main.main(["--mode", "commands"])
    [put] = worker["put"]
    assert put["min_price"] == 52400
    [(owner, text)] = worker["sent"]
    assert owner == "gf" and text.startswith("👀 Takip başladı") and "Kılıf" not in text
    meta = worker["metas"]["gf-1"]
    assert meta["shown_fp"] == main.fingerprint(put) and meta["seller_options"]
    assert worker["acked"] == [1]


def test_setup_job_with_no_results_deletes_and_says_so(worker, monkeypatch):
    worker["rows"] = [gf_watch()]
    worker["jobs"] = [{"id": 1, "type": "setup", "owner": "gf", "chat": "222", "watch": "gf-1"}]
    monkeypatch.setattr(main, "fetch", lambda w: [])
    monkeypatch.setattr(commands, "default_probe", lambda w: [])
    main.main(["--mode", "commands"])
    assert worker["deleted"] == ["gf-1"] and "bulamadım" in worker["replies"][0][1]


def test_show_summary_and_sellers_jobs(worker, monkeypatch):
    worker["rows"] = [gf_watch(min_price=50000),
                      {"id": "me-1", "owner": "me", "body": {"id": "me-1", "owner": "me", "query": "9070 xt"}, "meta": {}}]
    worker["jobs"] = [
        {"id": 1, "type": "show", "owner": "gf", "chat": "222", "watch": "gf-1"},
        {"id": 2, "type": "summary", "owner": "gf", "chat": "222"},
        {"id": 3, "type": "sellers", "owner": "gf", "chat": "222", "watch": "gf-1"},
        {"id": 4, "type": "show", "owner": "gf", "chat": "222", "watch": "me-1"},  # not hers
    ]
    monkeypatch.setattr(main, "fetch", lambda w: PHONES[:3])
    main.main(["--mode", "commands"])
    titles = [t.splitlines()[0].split(" · ")[0] for _, t in worker["sent"]]
    assert titles == ["📊 Şimdiki fiyatlar", "📋 Özet"] and all(o == "gf" for o, _ in worker["sent"])
    assert "9070" not in worker["sent"][1][1]  # her summary has only her watches
    assert worker["replies"][0][2] == [[("🏪 Satıcıları aç", "s:gf-1")]]
    assert worker["metas"]["gf-1"]["seller_options"][0] == ["Pttavm", 81997]
    assert worker["acked"] == [1, 2, 3, 4]


def test_akakce_down_during_a_job_tells_the_person(worker, monkeypatch):
    worker["rows"] = [gf_watch()]
    worker["jobs"] = [{"id": 1, "type": "setup", "owner": "gf", "chat": "222", "watch": "gf-1"}]

    def down(w):
        raise akakce.AkakceError("HTTP 503")
    monkeypatch.setattr(commands, "default_probe", down)
    main.main(["--mode", "commands"])
    assert "ulaşılamadı" in worker["replies"][0][1] and worker["acked"] == [1]


def test_without_worker_watches_yaml_is_used(monkeypatch):
    monkeypatch.delenv("INBOX_URL", raising=False)
    settings, watches, metas, from_worker = main.load_watches()
    assert not from_worker and watches and metas == {}


# ---------------------------------------------------------------- messages

def test_seller_shown_on_each_line():
    p = Product(1, "iPhone 17 256 GB", 86849, "u", 1, "Amazon Türkiye")
    assert main.product_line(p, {"query": "iphone 17"}, None) == "86.849₺  256 GB · Amazon Türkiye"


def test_seller_filter_reprices_and_reorders(monkeypatch):
    fake_offers(monkeypatch)
    state = {}
    out = main.apply_seller_filter([P(1, 81997), P(2, 84999), P(3, 89999)], ["amazon"], state, now=1000)
    assert [(p.code, p.price, p.seller) for p in out] == [(2, 84999, "Amazon Türkiye"),
                                                           (1, 86849, "Amazon Türkiye")]
    monkeypatch.setattr(akakce, "offers", lambda url: pytest.fail("should use cache"))
    assert [p.code for p in main.apply_seller_filter([P(1, 81997), P(2, 84999)], ["amazon"], state, now=1500)] == [2, 1]


def test_save_config_round_trip(tmp_path):
    f = tmp_path / "w.yaml"
    watches = [{"id": "gf-1", "owner": "gf", "query": "iphone 17", "include": ["iphone", "17"],
                "exclude": ["kılıf"], "sellers": ["Amazon Türkiye"], "min_price": 40900}]
    main.save_config(dict(SETTINGS), watches, f)
    assert f.read_text(encoding="utf-8").startswith("# Watch list")
    assert main.load_config(f)[1] == watches
