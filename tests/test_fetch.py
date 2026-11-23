"""fetch.py: ScraperAPI retry/credit matrix, Amazon's bot wall, and scrape_offer's rescue path.

Every test pins scraper.SCRAPERAPI_KEY itself: the real key from .env is loaded by
config, and an ambient value must never turn a unit test into a billed API call.

ScraperAPI's rates, confirmed against the account counter on 2026-11-23: 10 credits for
a page behind bot protection (salidzini, ceno), 5 for Amazon, 1 for an ordinary site.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import fetch
import fx
import scraper
import stores
from stores import presets

# salidzini's Turnstile page as ScraperAPI returns it from a flagged LV exit (2026-11-23)
TURNSTILE = ('<html><head><script src="https://challenges.cloudflare.com/turnstile/v0/api.js">'
             '</script></head><body>Are you human?</body></html>')
PAGE = "<html><body>the real page</body></html>"
GUARDED = 10          # credits per Cloudflare-guarded page — billed even for TURNSTILE

AMAZON_PRODUCT = """
<html><head><title>UGREEN LP923</title></head><body>
<div id="productTitle">UGREEN LP923</div>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price"><span class="a-offscreen">49,28\u00a0zł</span></span>
</div>
<div id="availability"><span>W magazynie</span></div>
</body></html>
"""

SALIDZINI_PAGE = """
<html><body><script type="application/ld+json">
{"@type":"Product","offers":{"@type":"AggregateOffer","offers":[
  {"price":12.38,"seller":{"name":"eltek.lv"}},{"price":12.59,"seller":{"name":"lukass.lv"}}]}}
</script></body></html>
"""


# ---------------------------------------------------------------------------
# ScraperAPI: fake session with queued outcomes
# ---------------------------------------------------------------------------

class FakeResponse:
    encoding = "utf-8"

    def __init__(self, status=200, text=PAGE, cost=None):
        self.status_code = status
        self.text = text
        self.headers = {} if cost is None else {"sa-credit-cost": str(cost)}


class FakeSession:
    """Hands out queued responses in order; a queued exception is raised instead."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.params: list[dict] = []

    @property
    def calls(self):
        return len(self.params)

    def get(self, url, params=None, timeout=None):
        self.params.append(params or {})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def api(monkeypatch):
    """Replaces the shared session and makes the retry back-offs instantaneous."""
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)

    def install(*outcomes):
        session = FakeSession(*outcomes)
        monkeypatch.setattr(fetch, "_session", session)
        return session
    return install


def bill(ledger):
    return lambda cost: ledger.append(cost)


def test_scraperapi_bills_the_credit_cost_the_response_reports(api):
    api(FakeResponse(cost=GUARDED))
    billed: list = []
    assert fetch.fetch_scraperapi("https://x.lv/", "k", on_credit=bill(billed)) == PAGE
    assert billed == [GUARDED]               # a Cloudflare-guarded page is not 1 credit


def test_scraperapi_credit_cost_falls_back_to_one(api):
    for response in (FakeResponse(), FakeResponse(cost="not-a-number")):
        billed: list = []
        api(response)
        fetch.fetch_scraperapi("https://x.lv/", "k", on_credit=bill(billed))
        assert billed == [1]                 # header absent or malformed


def test_scraperapi_flagged_exit_is_retried_and_every_call_billed(api):
    # measured 2026-11-23: ~1 in 4 salidzini calls through the LV pool gets a challenge
    # page. It is billed like a success, so the retry costs nothing per successful page.
    api(*[FakeResponse(text=TURNSTILE, cost=GUARDED)] * 2, FakeResponse(cost=GUARDED))
    billed: list = []
    html = fetch.fetch_scraperapi("https://x.lv/", "k", country="lv", on_credit=bill(billed))
    assert html == PAGE
    assert billed == [GUARDED] * 3


def test_scraperapi_gives_up_after_blocked_retries(api):
    session = api(*[FakeResponse(text=TURNSTILE, cost=GUARDED)] * (fetch.API_BLOCKED_RETRIES + 2))
    with pytest.raises(fetch.FetchError, match=r"HTTP 200 \(anti-bot page\)"):
        fetch.fetch_scraperapi("https://x.lv/", "k")
    assert session.calls == fetch.API_BLOCKED_RETRIES + 1


def test_scraperapi_rendered_challenge_is_not_retried(api):
    # bot protection + rendering = 20 credits a call: a challenge that outlives the
    # render wait is not worth another
    session = api(FakeResponse(text=TURNSTILE, cost=20), FakeResponse(text=TURNSTILE, cost=20))
    billed: list = []
    with pytest.raises(fetch.FetchError, match=r"HTTP 200 \(anti-bot page\)"):
        fetch.fetch_scraperapi("https://x.lv/", "k", render=True, on_credit=bill(billed))
    assert session.calls == 1 and billed == [20]


def test_scraperapi_4xx_raises_immediately_and_is_free(api):
    session = api(FakeResponse(status=403, text="nope", cost=GUARDED), FakeResponse())
    billed: list = []
    with pytest.raises(fetch.FetchError, match="HTTP 403"):
        fetch.fetch_scraperapi("https://x.lv/", "k", on_credit=bill(billed))
    assert session.calls == 1 and billed == []      # only 2xx responses are billed


def test_scraperapi_5xx_retried_once_and_is_free(api):
    # kurpirkt answered HTTP 500 on every tier (2026-11-19) — one retry, never billed
    session = api(FakeResponse(status=500), FakeResponse(status=500), FakeResponse())
    billed: list = []
    with pytest.raises(fetch.FetchError, match="HTTP 500"):
        fetch.fetch_scraperapi("https://x.lv/", "k", on_credit=bill(billed))
    assert session.calls == 2 and billed == []


def test_scraperapi_5xx_then_ok(api):
    api(FakeResponse(status=503), FakeResponse())
    assert fetch.fetch_scraperapi("https://x.lv/", "k") == PAGE


def test_scraperapi_network_error_retried_once(api):
    session = api(fetch.requests.ConnectionError("boom"), fetch.requests.ConnectionError("boom"))
    with pytest.raises(fetch.FetchError, match="network error"):
        fetch.fetch_scraperapi("https://x.lv/", "k")
    assert session.calls == 2


def test_scraperapi_block_and_error_retries_are_independent(api):
    # either order: each failure type spends its own budget, never the other's
    session = api(FakeResponse(text=TURNSTILE), fetch.requests.ConnectionError("blip"),
                  FakeResponse())
    assert fetch.fetch_scraperapi("https://x.lv/", "k") == PAGE
    assert session.calls == 3
    session = api(FakeResponse(status=500), FakeResponse(text=TURNSTILE),
                  FakeResponse(text=TURNSTILE), FakeResponse())
    assert fetch.fetch_scraperapi("https://x.lv/", "k") == PAGE
    assert session.calls == 4


def test_scraperapi_sends_render_and_country_params(api):
    session = api(FakeResponse())
    fetch.fetch_scraperapi("https://x.lv/", "k", render=True, country="lv")
    assert session.params[0] == {"api_key": "k", "url": "https://x.lv/", "render": "true",
                                 "wait": "8000", "country_code": "lv"}


# ---------------------------------------------------------------------------
# scrape_offer: the api_fallback rescue and usage accounting
# ---------------------------------------------------------------------------

def blocked_playwright(url, wait_selector="body"):
    raise fetch.FetchError("playwright TimeoutError: wait timed out")


def never_call_api(*args, **kwargs):
    raise AssertionError("ScraperAPI must not be called here")


def test_amazon_pl_rescues_a_blocked_playwright_attempt(monkeypatch):
    monkeypatch.setattr(scraper, "SCRAPERAPI_KEY", "k")
    monkeypatch.setattr(fetch, "fetch_playwright", blocked_playwright)

    def rescue(url, key, render=False, country=None, on_credit=None):
        if on_credit:
            on_credit(5)                       # ScraperAPI's rate for an Amazon page
        return AMAZON_PRODUCT

    monkeypatch.setattr(fetch, "fetch_scraperapi", rescue)
    monkeypatch.setattr(fx, "rate_to_eur", lambda c: 0.25)
    usage: dict = {}
    url = "https://www.amazon.pl/dp/B0D2Q5M35Y"
    err, ex = scraper.scrape_offer(url, stores.adapter_for(url), usage)
    assert err is None and ex is not None and ex.price == 12.32     # 49.28 PLN × 0.25
    assert usage["credits"] == 5                                    # the rescue, at its real cost


def test_no_rescue_without_the_flag_or_the_key(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_playwright", blocked_playwright)
    monkeypatch.setattr(fetch, "fetch_scraperapi", never_call_api)
    monkeypatch.setattr(scraper, "SCRAPERAPI_KEY", "k")
    url = "https://www.amazon.de/dp/B0TEST"              # amazon.de has no api_fallback
    err, ex = scraper.scrape_offer(url, stores.adapter_for(url), {})
    assert err == "playwright TimeoutError: wait timed out" and ex is None
    monkeypatch.setattr(scraper, "SCRAPERAPI_KEY", "")   # flagged preset, but no key
    url = "https://www.amazon.pl/dp/B0TEST"
    err, ex = scraper.scrape_offer(url, stores.adapter_for(url), {})
    assert err == "playwright TimeoutError: wait timed out" and ex is None


def test_only_amazon_pl_asks_for_the_rescue():
    assert presets.AMAZON_PL.api_fallback is True
    assert not presets.AMAZON_DE.api_fallback
    assert not stores.adapter_for("https://www.salidzini.lv/search?q=x").api_fallback


def test_dc_blocked_offer_bills_each_retry(api, monkeypatch):
    monkeypatch.setattr(scraper, "SCRAPERAPI_KEY", "k")
    session = api(FakeResponse(text=TURNSTILE, cost=GUARDED),
                  FakeResponse(text=SALIDZINI_PAGE, cost=GUARDED))
    usage: dict = {}
    url = "https://www.salidzini.lv/search?q=UGREEN+LP923"
    err, ex = scraper.scrape_offer(url, stores.adapter_for(url), usage)
    assert err is None and ex is not None and ex.price == 12.38 and ex.shop == "eltek.lv"
    assert session.calls == 2 and session.params[-1]["country_code"] == "lv"
    assert usage["credits"] == 2 * GUARDED            # the challenge page was billed too


# ---------------------------------------------------------------------------
# The bot wall: fake page objects (no browser needed)
# ---------------------------------------------------------------------------

class FakeEl:
    def __init__(self, label, broken=False):
        self.label, self.broken, self.clicked = label, broken, 0

    def evaluate(self, _js):
        if self.broken:
            raise RuntimeError("node is detached")
        return self.label

    def click(self, timeout=None):
        self.clicked += 1


class FakeLocator:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0]


class FakePage:
    def __init__(self, body, controls=None, title="Amazon.pl"):
        self.body, self.controls, self.loads = body, controls or {}, 0
        self._title = title
        self.url = "https://www.amazon.pl/"

    def title(self):
        return self._title

    def inner_text(self, _sel):
        return self.body

    def locator(self, sel):
        return FakeLocator(self.controls.get(sel, []))

    def wait_for_load_state(self, _state):
        self.loads += 1


def test_wall_clicks_the_labeled_submit_input():
    # <input type=submit> has no inner text — its label lives in the value attribute
    search, wall = FakeEl("Szukaj"), FakeEl(" Kontynuuj zakupy ")
    page = FakePage("Aby kontynuować, kliknij poniższy przycisk",
                    {"input[type=submit]": [search, wall]})
    assert fetch._dismiss_bot_wall(page) is True
    assert wall.clicked == 1 and search.clicked == 0 and page.loads == 1


def test_wall_without_labeled_control_falls_back_to_the_first_one():
    button = FakeEl("OK")
    page = FakePage("Weiter shoppen", {"input[type=submit]": [], "button": [button]})
    assert fetch._dismiss_bot_wall(page) is True and button.clicked == 1


def test_no_wall_means_no_click():
    button = FakeEl("Kontynuuj zakupy")      # the words alone, without the wall text
    page = FakePage("A normal product page", {"button": [button]})
    assert fetch._dismiss_bot_wall(page) is False and button.clicked == 0


def test_a_detached_control_does_not_abort_the_scan():
    broken, wall = FakeEl("", broken=True), FakeEl("Kontynuuj zakupy")
    page = FakePage("kliknij poniższy przycisk", {"button": [broken, wall]})
    assert fetch._dismiss_bot_wall(page) is True and wall.clicked == 1


def test_landed_on_reports_url_and_title_and_never_raises():
    assert fetch._landed_on(FakePage("")) == " [landed on https://www.amazon.pl/ — 'Amazon.pl']"

    class Dead:
        @property
        def url(self):
            raise RuntimeError("target closed")

    assert fetch._landed_on(Dead()) == ""


# ---------------------------------------------------------------------------
# The bot wall end to end: a local stand-in for Amazon.pl + real headless Chromium
# (skipped where no browser binary is installed, e.g. the CI test job)
# ---------------------------------------------------------------------------

WALL = """<html><head><title>Amazon.pl</title></head><body>
<p>Aby kontynuować, kliknij poniższy przycisk</p>
<form method="post" action="/wall-pass"><input type="submit" value="Kontynuuj zakupy"></form>
</body></html>"""

# Where the wall button really lands (since 2026-11): the storefront — no product here.
STOREFRONT = """<html><head><title>Amazon.pl. Niskie ceny</title></head>
<body><h1>Sklep</h1></body></html>"""


class WallHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # keep the test output quiet
        pass

    def _send(self, body, status=200, headers=()):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        cleared = "cleared=1" in (self.headers.get("Cookie") or "")
        if self.path.startswith("/dp/"):
            self._send(AMAZON_PRODUCT if cleared else WALL)   # the wall until cleared
        else:
            self._send(STOREFRONT)

    def do_POST(self):
        # the click sets the clearance cookie, then bounces to the storefront root
        self._send("", 302, (("Set-Cookie", "cleared=1; Path=/"), ("Location", "/")))


@pytest.fixture(scope="module")
def amazon():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True, args=["--no-sandbox"]).close()
    except Exception as e:  # noqa: BLE001 — no browser binary here → nothing to exercise
        pytest.skip(f"Playwright Chromium not installed ({type(e).__name__})")
    server = ThreadingHTTPServer(("127.0.0.1", 0), WallHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def fx_offline(monkeypatch):
    monkeypatch.setattr(fx, "rate_to_eur", lambda c: 0.25)


def test_wall_button_lands_on_storefront_so_fetch_returns_to_the_deep_link(amazon, fx_offline):
    url = f"{amazon}/dp/B0D2Q5M35Y"
    html = fetch.fetch_playwright(url, presets.AMAZON_PL.wait_selector)
    assert "Sklep" not in html                 # not left on the storefront
    ex = presets.AMAZON_PL.extract(html)
    assert ex is not None and ex.display == "49,28\u00a0zł" and ex.in_stock is True


def test_wait_timeout_error_names_the_page_it_was_left_on(amazon, monkeypatch):
    monkeypatch.setattr(fetch, "PLAYWRIGHT_WAIT_MS", 700)       # don't sit out 2 × 15 s
    url = f"{amazon}/no-product-here"
    with pytest.raises(fetch.FetchError) as raised:
        fetch.fetch_playwright(url, presets.AMAZON_PL.wait_selector)
    assert "Timeout" in str(raised.value)
    assert f"[landed on {url} — 'Amazon.pl. Niskie ceny']" in str(raised.value)
