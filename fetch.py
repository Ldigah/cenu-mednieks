"""HTTP fetching: requests first, Playwright fallback for blocked / JS-rendered stores."""
from __future__ import annotations

import time

import requests

from config import BASE_HEADERS, FETCH_RETRIES, REQUEST_TIMEOUT, USER_AGENT

BLOCK_MARKERS = (
    "enter the characters you see below",
    "robot check",
    "api-services-support@amazon.com",
    "attention required",
    "sorry, we just need to make sure you're not a robot",
    "ar jūs ne robotas",   # pigu.lt throttling page
    "hcaptcha.com",        # salidzini.lv slug URLs
    "challenges.cloudflare.com/turnstile",   # salidzini.lv / kurpirkt.lv walls
)

# Shared session: cookie reuse helps Cloudflare-fronted shops (pigu.lt).
_session = requests.Session()


class FetchError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _lang_for(url: str) -> str:
    if "amazon.pl" in url:
        return "pl-PL,pl;q=0.9,en;q=0.8"
    if "amazon." in url:
        return "de-DE,de;q=0.9,en;q=0.8"
    return BASE_HEADERS["Accept-Language"]


def _looks_blocked(html: str) -> bool:
    low = html[:4000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def _force_utf8(r: requests.Response) -> None:
    """Sites without a charset header default to latin-1 in requests, which garbles
    € ("99,99Â â¬"). EU retail pages are utf-8 — override the legacy default."""
    if r.encoding is None or r.encoding.upper() in ("ISO-8859-1", "LATIN1", "LATIN-1"):
        r.encoding = "utf-8"


def fetch_requests(url: str) -> str:
    """Plain HTTP GET with retries; raises FetchError on failure/block."""
    last: FetchError | None = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            r = _session.get(url, headers={**BASE_HEADERS, "Accept-Language": _lang_for(url)},
                             timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last = FetchError(f"network error: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        _force_utf8(r)
        if r.status_code == 200 and not _looks_blocked(r.text):
            return r.text
        blocked = " (anti-bot page)" if _looks_blocked(r.text) else ""
        last = FetchError(f"HTTP {r.status_code}{blocked}", r.status_code)
        # Cloudflare throttling (pigu.lt) needs a long cooldown, not 2 s
        time.sleep(45 if r.status_code in (403, 429) else 2 * (attempt + 1))
    assert last is not None  # at least one attempt always assigns it
    raise last


BOT_WALL_PHRASES = ("weiter shoppen", "kontynuuj zakupy", "continue shopping",
                    "kliknij poniższy przycisk", "klicke auf die schaltfläche")

CAPTCHA_TITLES = ("robot check", "attention required", "just a moment", "access denied",
                  "ne robotas")


def fetch_scraperapi(url: str, api_key: str, render: bool = False) -> str:
    """Fetch via ScraperAPI (rotating proxy pool) — for Cloudflare-guarded
    aggregators that challenge datacenter IPs (CI).

    Credit cost: 1 credit plain, 10 with render=true. Rendered fetches add
    wait=8000 so CF managed challenges (kurpirkt clears ≤10 s in a real
    browser) have time to resolve. Their API queues slow sites, so plain
    fetches get 60 s and rendered 100 s. Retries: once on pure network
    timeouts and once on their 5xx (render-worker hiccups — seen as HTTP 500);
    4xx/anti-bot responses raise immediately.
    """
    params: dict = {"api_key": api_key, "url": url}
    if render:
        params["render"] = "true"
        params["wait"] = "8000"
    timeout = 100 if render else 60
    last: FetchError | None = None
    for _attempt in range(2):
        try:
            r = _session.get("https://api.scraperapi.com/", params=params, timeout=timeout)
        except requests.RequestException as e:
            last = FetchError(f"scraperapi network error: {e}")
            time.sleep(5)
            continue
        _force_utf8(r)
        if r.status_code == 200 and not _looks_blocked(r.text):
            return r.text
        if r.status_code >= 500 and _attempt == 0:
            last = FetchError(f"scraperapi HTTP {r.status_code}", r.status_code)
            time.sleep(10)
            continue
        blocked = " (anti-bot page)" if _looks_blocked(r.text) else ""
        raise FetchError(f"scraperapi HTTP {r.status_code}{blocked}", r.status_code)
    assert last is not None  # both attempts failed with a retryable error
    raise last


def _wait_captcha_clear(page, timeout_s: int = 10) -> bool:
    """Turnstile/CF managed challenges often auto-clear in a real engine — give them a chance."""
    import time as _t
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        title = (page.title() or "").lower()
        if not any(t in title for t in CAPTCHA_TITLES):
            return True
        _t.sleep(2)
    return False


def _dismiss_bot_wall(page) -> None:
    """Amazon serves a clickable bot-check interstitial — click through it if present.

    Click the control whose own text matches the wall ("Continue shopping" etc.);
    the first <a> on such pages is usually the logo and leads nowhere useful.
    """
    try:
        body = (page.inner_text("body") or "").lower()[:2000]
        if not any(k in body for k in BOT_WALL_PHRASES):
            return
        for sel in ("input[type=submit]", "button", "a"):
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                txt = (el.inner_text() or "").strip().lower()
                if txt and any(p in txt for p in BOT_WALL_PHRASES):
                    el.click(timeout=3000)
                    page.wait_for_load_state("domcontentloaded")
                    return
        # no labeled control found — old fallback: first submit control
        for sel in ("input[type=submit]", "button", "a"):
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                return
    except Exception:  # noqa: BLE001 — best effort; never break the fetch
        pass


def fetch_playwright(url: str, wait_selector: str = "body") -> str:
    """Headless Chromium fetch — for stores that block plain requests or render via JS."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise FetchError("playwright not installed "
                         "(pip install playwright && playwright install chromium)") from e

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            try:
                last_err = None
                for _attempt in (1, 2):   # Amazon blocking is probabilistic — a retry pays off
                    ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                              viewport={"width": 1366, "height": 900})
                    try:
                        page = ctx.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                        title = (page.title() or "").lower()
                        if any(t in title for t in CAPTCHA_TITLES):
                            if not _wait_captcha_clear(page):
                                raise FetchError(  # deterministic block — retrying won't help
                                    f"captcha page (title={page.title()!r})")
                        _dismiss_bot_wall(page)
                        page.wait_for_selector(wait_selector, timeout=15_000)
                        html = page.content()
                        if _looks_blocked(html):
                            raise FetchError("anti-bot page after render", 403)
                        return html
                    except FetchError as e:
                        last_err = e
                        if "captcha" in str(e):
                            raise           # don't retry hard captchas
                    except Exception as e:  # noqa: BLE001 — timeouts etc. get one retry
                        last_err = FetchError(f"playwright {type(e).__name__}: {e}")
                    finally:
                        ctx.close()
                assert last_err is not None  # every attempt assigns it
                raise last_err
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001 — Playwright raises its own error types
        raise FetchError(f"playwright {type(e).__name__}: {e}") from e
