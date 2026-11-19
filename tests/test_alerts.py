import alerts
from alerts import diff_events
from parse import Extraction

ENTRY = {"id": "x", "name": "JBL Flip 7", "store_label": "Amazon.de",
         "url": "https://example.org/p"}


def test_first_sighting_no_alert():
    assert diff_events(ENTRY, Extraction(price=100.0), None) == []


def test_drop_alert():
    events = diff_events(ENTRY, Extraction(price=100.0), {"price": 120.0})
    assert len(events) == 1 and "100.00" in events[0] and "16.7" in events[0]


def test_small_drop_below_threshold_no_alert():
    assert diff_events(ENTRY, Extraction(price=100.0), {"price": 100.5}) == []


def test_increase_silent():
    assert diff_events(ENTRY, Extraction(price=150.0), {"price": 100.0}) == []


def test_discount_appears():
    events = diff_events(ENTRY, Extraction(price=100.0, old_price=150.0), {"price": 100.0})
    assert len(events) == 1 and "reg. 150.00" in events[0]


def test_discount_deepens():
    old = {"price": 110.0, "old_price": 140.0}   # 30 € off before
    new = Extraction(price=100.0, old_price=150.0)  # 50 € off now
    events = diff_events(ENTRY, new, old)
    # the price also dropped 110 → 100, so both a drop event and a discount event fire
    assert len(events) == 2
    assert any("100.00" in e for e in events)        # drop line
    assert any("reg. 150.00" in e for e in events)   # discount line


def test_discount_unchanged_no_alert():
    old = {"price": 100.0, "old_price": 150.0}
    new = Extraction(price=100.0, old_price=150.0)
    assert diff_events(ENTRY, new, old) == []


def test_restock_alert():
    events = diff_events(ENTRY, Extraction(price=99.0, in_stock=True),
                         {"price": 99.0, "in_stock": False})
    assert len(events) == 1 and "back in stock" in events[0]


# --- product-level events (2026-11-19 rework) ---

PRODUCT: dict = {"id": "jbl-flip-7", "name": "JBL Flip 7", "threshold_pct": None, "offers": []}
OFFER = {"url": "https://www.salidzini.lv/search?q=x", "store": "salidzini.lv",
         "label": "salidzini.lv"}


def test_product_min_threshold_default():
    prev = {"price": 100.0, "store": "a.lv"}
    assert alerts.product_min_event(PRODUCT, OFFER, Extraction(price=97.0), prev) is None  # -3% < 5%
    ev = alerts.product_min_event(PRODUCT, OFFER, Extraction(price=94.0), prev)            # -6% ≥ 5%
    assert ev and "94.00" in ev and "salidzini.lv" in ev and "94" in ev


def test_product_min_threshold_override():
    prod = dict(PRODUCT, threshold_pct=10.0)
    assert alerts.product_min_event(prod, OFFER, Extraction(price=94.0), {"price": 100.0}) is None
    assert alerts.product_min_event(prod, OFFER, Extraction(price=89.0), {"price": 100.0})


def test_atl_first_sighting_silent_then_alert():
    prod = dict(PRODUCT)
    assert alerts.atl_update(prod, OFFER, Extraction(price=73.0)) is None
    assert prod["atl"]["price"] == 73.0                       # baseline recorded silently
    assert alerts.atl_update(prod, OFFER, Extraction(price=75.0)) is None
    ev = alerts.atl_update(prod, OFFER, Extraction(price=71.9))
    assert ev and "71.90" in ev and "all-time low" in ev
    assert prod["atl"]["price"] == 71.9


def test_offer_events_skip_drops():
    old = {"price": 100.0}
    assert alerts.offer_events(ENTRY, Extraction(price=90.0), old) == []   # drops are product-level now
    evs = alerts.offer_events(ENTRY, Extraction(price=100.0, old_price=150.0), {"price": 100.0})
    assert len(evs) == 1 and "discount" in evs[0]


def test_digest_enriched():
    offer = {"url": "https://s.lv/x", "store": "s.lv", "label": "s.lv"}
    products = {"version": 2, "products": [dict(PRODUCT, offers=[offer])]}
    prices = {"https://s.lv/x": {"price": 71.9, "in_stock": True, "shop": "24.lv",
                                 "history": [{"t": "2026-11-19T10:00:00+00:00", "p": 71.9}]}}
    lines = alerts.digest_lines(products, prices)
    assert any("71.90" in ln and "s.lv" in ln and "1 store" in ln for ln in lines)


def test_health_summary_counts_offers():
    offer = {"url": "https://s.lv/x", "store": "s.lv", "label": "s.lv"}
    products = {"version": 2, "products": [dict(PRODUCT, offers=[offer])]}
    ok = alerts.health_summary(products, {})
    assert "all 1 offers OK" in ok
    bad = alerts.health_summary(products, {"https://s.lv/x": {"fails": 3}})
    assert "1/1 offers failing" in bad and "×3" in bad
