from parse import Extraction
from alerts import diff_events

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
