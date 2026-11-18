from commands import find_entries, make_entry, parse_command, slugify

PRODUCTS = {"products": [
    {"id": "jbl-flip-7-amazon-de", "name": "JBL Flip 7", "store_label": "amazon.de",
     "url": "https://www.amazon.de/dp/B0TEST"},
    {"id": "ugreen-lp923-salidzini-lv", "name": "UGREEN LP923", "store_label": "salidzini.lv",
     "url": "https://www.salidzini.lv/x"},
]}


def test_parse_command_basic():
    assert parse_command("/list") == ("list", "")
    assert parse_command("/add https://x.ee My thing") == ("add", "https://x.ee My thing")
    assert parse_command("plain text") is None
    assert parse_command("") is None
    assert parse_command("/") is None


def test_parse_command_bot_suffix():
    assert parse_command("/prices@CenuMednieksBot") == ("prices", "")


def test_make_entry_known_store():
    entry, note = make_entry("https://www.amazon.de/dp/B0XYZ", "JBL Flip 7")
    assert note == ""
    assert entry["id"] == "jbl-flip-7-amazon-de"
    assert entry["store_label"] == "amazon.de"
    assert entry["url"].startswith("https://")


def test_make_entry_unknown_store_gets_auto_adapter():
    entry, note = make_entry("https://www.eshop.example/product/1", "Widget")
    assert entry is not None and "JSON-LD" in note


def test_make_entry_bad_url():
    entry, note = make_entry("not a url")
    assert entry is None and note


def test_make_entry_adds_scheme():
    entry, _ = make_entry("salidzini.lv/cenas/jbl-flip-7", "JBL")
    assert entry is not None and entry["url"].startswith("https://")


def test_slugify():
    assert slugify("UGREEN LP923 — laptop stand!") == "ugreen-lp923-laptop-stand"


def test_find_entries():
    assert len(find_entries(PRODUCTS, "jbl")) == 1
    assert len(find_entries(PRODUCTS, "lp923")) == 1
    assert find_entries(PRODUCTS, "nonexistent") == []
    assert find_entries(PRODUCTS, "") == []
