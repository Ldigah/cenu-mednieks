import catalog
from parse import Extraction

V1 = {"products": [
    {"id": "jbl-flip-7-amazon-de", "name": "JBL Flip 7", "store_label": "amazon.de",
     "url": "https://www.amazon.de/dp/B0TEST", "added": "2026-01-01T00:00:00+00:00"},
    {"id": "jbl-flip-7-salidzini", "name": "JBL Flip 7", "store_label": "salidzini.lv",
     "url": "https://www.salidzini.lv/search?q=JBL+Flip+7", "added": "2026-01-01T00:00:00+00:00"},
    {"id": "ugreen-salidzini", "name": "", "store_label": "salidzini.lv",
     "url": "https://www.salidzini.lv/search?q=ugreen"},
]}


def test_migrate_groups_by_name():
    out = catalog.migrate_products(V1)
    assert out["version"] == 2
    by_id = {p["id"]: p for p in out["products"]}
    assert set(by_id) == {"jbl-flip-7", "ugreen-salidzini"}
    jbl = by_id["jbl-flip-7"]
    assert jbl["name"] == "JBL Flip 7"
    assert len(jbl["offers"]) == 2
    assert jbl["offers"][0]["store"] == "amazon.de"
    assert jbl["threshold_pct"] is None and jbl["atl"] is None


def test_migrate_v2_passthrough_and_dedupe():
    v2 = {"version": 2, "products": [{"id": "x", "name": "X", "offers": [
        {"url": "https://a.example/1"}, {"url": "https://a.example/1"}]}]}
    out = catalog.migrate_products(v2)
    assert len(out["products"]) == 1
    assert len(out["products"][0]["offers"]) == 1          # duplicate URL dropped
    assert out["products"][0]["offers"][0]["store"] == "a.example"


def test_looks_like_url():
    assert catalog.looks_like_url("https://x.lv/1")
    assert catalog.looks_like_url("salidzini.lv/search?q=x")
    assert not catalog.looks_like_url("JBL Flip 7")
    assert not catalog.looks_like_url("")
    assert not catalog.looks_like_url("two words.lv")


def test_make_offer():
    offer, note = catalog.make_offer("www.amazon.de/dp/B0X")
    assert offer is not None
    assert offer["url"].startswith("https://") and offer["store"] == "amazon.de"
    assert note == ""
    offer2, note2 = catalog.make_offer("https://unknown.example/p/1")
    assert offer2 is not None and "JSON-LD" in note2
    bad, err = catalog.make_offer("not a url")
    assert bad is None and err


def test_discovery_offers():
    offers = catalog.discovery_offers("JBL Flip 7")
    assert [o["store"] for o in offers] == ["salidzini.lv", "kurpirkt.lv", "ceno.lv"]
    assert "q=JBL+Flip+7" in offers[0]["url"]


def test_find_and_add_offer():
    prods = {"products": [catalog.make_product("JBL Flip 7",
                                               catalog.discovery_offers("JBL Flip 7"))]}
    assert len(catalog.find_products(prods, "flip")) == 1
    assert catalog.find_products(prods, "zzz") == []
    offer, _ = catalog.make_offer("https://www.amazon.de/dp/B0TEST")
    assert offer is not None
    assert catalog.add_offer(prods["products"][0], offer) is True
    assert catalog.add_offer(prods["products"][0], offer) is False   # duplicate URL
    assert prods["products"][0]["offers"][-1]["store"] == "amazon.de"


def test_best_result_and_best_state():
    o1 = {"url": "1", "store": "a", "label": "a"}
    o2 = {"url": "2", "store": "b", "label": "b"}
    results = [(o1, Extraction(price=80.0)), (o2, Extraction(price=73.0))]
    offer, ex = catalog.best_result(results)
    assert offer is o2 and ex is not None and ex.price == 73.0
    prod = catalog.make_product("x", [o1, o2])
    bo, bst = catalog.best_state(prod, {"1": {"price": 80.0}, "2": {"price": 73.0}})
    assert bo is not None and bo["url"] == "2" and bst is not None and bst["price"] == 73.0
    assert catalog.best_state(prod, {}) == (None, None)


def test_offer_entry_shape():
    prod = catalog.make_product("JBL Flip 7", [])
    e = catalog.offer_entry(prod, {"url": "https://x/1", "store": "a.lv", "label": "A.lv"})
    assert e["name"] == "JBL Flip 7"
    assert e["store_label"] == "A.lv"
    assert e["url"] == "https://x/1"


def test_product_stats_windows():
    from datetime import UTC, datetime, timedelta
    o1 = {"url": "1", "store": "a", "label": "a"}
    prod = catalog.make_product("x", [o1])
    now = datetime.now(UTC)
    pts = [(now - timedelta(days=d), 100.0 + d) for d in range(40)]  # older = higher
    prices = {"1": {"price": 100.0, "history": [
        {"t": t.isoformat(), "p": p} for t, p in pts]}}
    s = catalog.product_stats(prod, prices, now=now)
    assert s["best"]["price"] == 100.0
    assert s["min7"] == 100.0                      # newest points are cheapest
    assert s["min30"] == 100.0
    assert s["avg30"] and 100.0 < s["avg30"] < 140.0
    assert s["points"] == 40 and s["stores"] == 1
    empty = catalog.product_stats(catalog.make_product("y", [o1]), {"1": {}}, now=now)
    assert empty["min7"] is None and empty["best"]["price"] is None
