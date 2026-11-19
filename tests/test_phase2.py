"""Phase-2 features: targets (Q21), tags (Q24), derived stats (Q23), i18n (Q16),
alert specs, /search stager mode (Q27), publish data (Q33), parse image (Q28)."""
import json
from datetime import UTC, datetime, timedelta

import alerts
import catalog
import i18n
import parse
import users
from commands import parse_price_arg

# ---------------------------------------------------------------------------
# targets (Q21)
# ---------------------------------------------------------------------------

def test_set_clear_target_and_hit_bookkeeping():
    u = users.new_user(1)
    users.set_target(u, "jbl", 65.0)
    assert users.target_for(u, "jbl") == 65.0
    # above target → no alert + hit record cleared
    assert users.target_status(u, "jbl", 70.0) == "above"
    assert "jbl" not in (u.get("targets_hit") or {})
    # crossing → new-low (fires once)
    assert users.target_status(u, "jbl", 64.0) == "new-low"
    # same low again → hit-already (no re-alert)
    assert users.target_status(u, "jbl", 64.0) == "hit-already"
    # lower low → fires again
    assert users.target_status(u, "jbl", 63.0) == "new-low"
    # price rises above → bookkeeping resets
    assert users.target_status(u, "jbl", 70.0) == "above"
    assert "jbl" not in (u.get("targets_hit") or {})
    assert users.clear_target(u, "jbl") is True
    assert users.clear_target(u, "jbl") is False
    assert users.target_for(u, "jbl") is None


def test_targets_merge_roundtrip():
    u = users.new_user(1)
    users.set_target(u, "jbl", 65.0)
    ud = users.ensure({"users": [dict(u, chat_id=1)]})
    u1 = users.get(ud, 1)
    assert u1 is not None and u1["targets"] == {"jbl": 65.0}
    merged = users.merge_users(users.ensure({"users": []}), ud)
    m1 = users.get(merged, 1)
    assert m1 is not None and m1["targets"] == {"jbl": 65.0}


def test_parse_price_arg():
    assert parse_price_arg("65") == 65.0
    assert parse_price_arg("65,99 €") == 65.99
    assert parse_price_arg("12.5") == 12.5
    assert parse_price_arg("abc") is None
    assert parse_price_arg("") is None


# ---------------------------------------------------------------------------
# tags (Q24)
# ---------------------------------------------------------------------------

def products_for_tags():
    return {"products": [
        {"id": "jbl-flip-7", "name": "JBL Flip 7", "tags": ["audio", "speaker"], "offers": []},
        {"id": "ugreen", "name": "UGREEN", "tags": ["accessory"], "offers": []},
        {"id": "anker", "name": "Anker", "tags": ["audio"], "offers": []},
    ]}


def test_tag_helpers_and_index():
    prods = products_for_tags()
    assert catalog.tag_index(prods) == {"accessory": 1, "audio": 2, "speaker": 1}
    assert [p["id"] for p in catalog.find_tagged(prods, "#audio")] == \
        ["jbl-flip-7", "anker"]
    assert [p["id"] for p in catalog.find_tagged(prods, "")] == \
        ["jbl-flip-7", "ugreen", "anker"]
    assert catalog.set_tag(prods["products"][0], "#Outdoor") is True
    assert "outdoor" in prods["products"][0]["tags"]
    assert catalog.set_tag(prods["products"][0], "outdoor") is False  # dedupe
    assert catalog.unset_tag(prods["products"][0], "outdoor") is True


def test_find_products_matches_tags():
    prods = products_for_tags()
    assert [p["id"] for p in catalog.find_products(prods, "#speaker")] == ["jbl-flip-7"]


# ---------------------------------------------------------------------------
# derived stats (Q23)
# ---------------------------------------------------------------------------

def test_volatility_drops_and_weekday():
    now = datetime.now(UTC).timestamp()
    pts = []
    # 6 weeks of weekly lows: Mon cheap, Fri expensive → Monday should win
    for week in range(6):
        monday = now - (6 - week) * 7 * 86400
        pts.append((monday, 90.0 + week))
        pts.append((monday + 3 * 86400, 100.0 + week))
    vol = catalog.volatility(pts, now)
    assert vol is not None and vol > 0
    dow = catalog.best_weekday(pts, now)
    assert dow is not None and dow["day"] == 0  # Monday
    assert dow["avg"] < 100
    # few points → honest None
    assert catalog.volatility(pts[:3], now) is None
    assert catalog.best_weekday(pts[:4], now) is None


def test_drop_count():
    now = datetime.now(UTC).timestamp()
    pts = [(now - 10 * 86400, 100.0), (now - 8 * 86400, 94.0),   # −6% drop #1
           (now - 6 * 86400, 94.5),                              # wiggle up (no reset)
           (now - 4 * 86400, 88.0),                              # −6.4% vs 94 floor #2
           (now - 1 * 86400, 87.8)]                              # small change, not counted
    assert catalog.drop_count(pts, now, min_pct=5.0) == 2
    assert catalog.drop_count(pts[:1], now) is None


def test_product_stats_carries_new_fields():
    now = datetime.now(UTC)
    prod = {"id": "p", "name": "P", "offers": [{"url": "u", "store": "s", "label": "s"}],
            "tags": ["audio"], "atl": {"price": 50.0, "date": "2026-01-01"}}
    prices = {"u": {"price": 60.0, "history": [
        {"t": (now - timedelta(days=i)).isoformat(), "p": 60.0 + (i % 4)} for i in range(10)]}}
    s = catalog.product_stats(prod, prices, now=now)
    assert s["vol"] is not None
    assert s["drops30"] is not None
    assert set(s) >= {"best", "atl", "min7", "min30", "avg30", "vol", "dow", "drops30"}


# ---------------------------------------------------------------------------
# i18n (Q16) + alert specs
# ---------------------------------------------------------------------------

def test_t_fallback_chain():
    assert i18n.T("lv", "digest_header", date="2026-01-01").startswith("🛒")
    assert i18n.T("en", "digest_header", date="2026-01-01").startswith("🛒")
    assert i18n.T("de", "digest_header", date="2026-01-01").startswith("🛒")  # → en
    assert i18n.T("en", "nonexistent_key") == "nonexistent_key"
    assert i18n.T("lv", "watching", name="x").startswith("⭐ Sekoju <b>x</b>")


def test_alert_specs_match_legacy_triggers():
    from parse import Extraction
    product = {"id": "p", "name": "JBL", "threshold_pct": None}
    offer = {"url": "u", "store": "24.lv", "label": "24.lv"}
    prev = {"price": 100.0}
    new = Extraction(price=90.0)
    ev = alerts.product_min_event(product, offer, new, prev)
    spec = alerts.product_min_spec(product, offer, new, prev)
    assert ev is not None and spec is not None and spec[0] == "drop_alert"
    assert "90.00 €" in spec[1]["price"]
    # below threshold → both silent
    small = Extraction(price=99.5)
    assert alerts.product_min_event(product, offer, small, prev) is None
    assert alerts.product_min_spec(product, offer, small, prev) is None


def test_lv_rendering_of_specs():
    from parse import Extraction
    product = {"id": "p", "name": "JBL", "threshold_pct": None}
    offer = {"url": "u", "store": "24.lv", "label": "24.lv"}
    spec = alerts.product_min_spec(product, offer, Extraction(price=90.0),
                                   {"price": 100.0})
    assert spec is not None
    text = i18n.T("lv", spec[0], **spec[1])
    assert "jauna labākā cena" in text and "90.00 €" in text


# ---------------------------------------------------------------------------
# guided /search mode (Q27)
# ---------------------------------------------------------------------------

def test_stage_add_search_mode_labels(monkeypatch):
    import scraper
    import telegram_io
    sent = []

    class FakeTG:
        def post(self, url, json=None, timeout=None, **kw):
            sent.append(json)

            class R:
                ok = True

                def json(self):
                    return {"ok": True}

            return R()

    monkeypatch.setattr(telegram_io.requests, "post", FakeTG().post)
    monkeypatch.setattr(scraper, "_fetch_html", lambda url, adapter: "")
    from stores import base
    monkeypatch.setattr(scraper.stores, "adapter_for",
                        lambda url: base.StoreAdapter(domain="t", label="t",
                                                      strategy="requests"))
    monkeypatch.setattr(base.StoreAdapter, "candidates",
                        lambda self, html, limit=3: [
                            {"name": "Anker Soundcore 2", "min_price": 35.0, "count": 9}])
    bot_state: dict = {}
    scraper.stage_add(2, "anker", {"chat_id": 2}, bot_state, search_mode=True)
    card = [j for j in sent if "reply_markup" in j][0]
    assert "Results for" in card["text"]          # search wording, not "Matches for"
    btns = [b["text"] for row in card["reply_markup"]["inline_keyboard"] for b in row]
    assert any(b.startswith("⭐") for b in btns)  # ⭐ Track labels
    assert "pending_adds" in bot_state


# ---------------------------------------------------------------------------
# image extraction (Q28)
# ---------------------------------------------------------------------------

def test_jsonld_image_extraction():
    html = """<html><head><script type="application/ld+json">{
     "@type":"Product","name":"JBL","image":["https://img.example/jbl.jpg"],
     "offers":{"@type":"Offer","price":"73.00","priceCurrency":"EUR"}}</script></head>"""
    from bs4 import BeautifulSoup
    ex = parse.jsonld_extraction(BeautifulSoup(html, "html.parser"))
    assert ex is not None and ex.image == "https://img.example/jbl.jpg"
    assert ex.price == 73.0


def test_og_image_fallback_in_css_path():
    from stores.base import StoreAdapter
    html = """<html><head><meta property="og:image" content="https://i.example/x.jpg">
    </head><body><span id="p">89,99 €</span></body></html>"""
    ad = StoreAdapter(domain="x", label="x", strategy="requests",
                      selectors={"price": "#p"})
    ex = ad.extract(html)
    assert ex is not None and ex.image == "https://i.example/x.jpg"


# ---------------------------------------------------------------------------
# publish data (Q33/Q14): public json has NO user data
# ---------------------------------------------------------------------------

def test_build_data_public_and_enriched(tmp_path, monkeypatch):
    import publish
    products = {"version": 2, "products": [{
        "id": "jbl-flip-7", "name": "JBL Flip 7", "tags": ["audio"],
        "image": "https://img.example/j.jpg", "threshold_pct": 5.0,
        "atl": {"price": 70.0, "date": "2026-01-01"},
        "added_by": 497422706,
        "offers": [{"url": "https://s.lv/x", "store": "s.lv", "label": "s.lv"}]}]}
    prices = {"https://s.lv/x": {"price": 73.0, "in_stock": True}}
    monkeypatch.delenv("APP_API_URL", raising=False)
    data = publish.build_data(products, prices)
    blob = json.dumps(data)
    assert "497422706" not in blob and "added_by" not in blob  # Q33: no user data
    entry = data["products"][0]
    assert entry["image"] == "https://img.example/j.jpg"
    assert entry["tags"] == ["audio"]
    assert entry["above_atl"] == 4  # (73−70)/70 ≈ 4.3% → rounded 4
    assert entry["chart"] == "charts/jbl-flip-7.png"
    assert entry["offers"][0]["price"] == 73.0
    assert data["tags"] == {"audio": 1}
    assert data["api_base"].startswith("https://")
