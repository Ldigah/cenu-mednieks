"""HTTP fetching: requests first, Playwright fallback for blocked / JS-rendered stores."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests

from config import BASE_HEADERS, FETCH_RETRIES, REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger("tracker")   # scraper.py configures this logger

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

PLAYWRIGHT_WAIT_MS = 15_000       # how long the wait selector may take to appear


# A challenge page can come back as HTTP 200 when the rotating pool hands out a
# flagged exit (measured 2026-11-23: 3 of 15 salidzini calls through country_code=lv
# got its Turnstile page). ScraperAPI bills that page like a success, so one more
# call — on another exit — turns a paid-for failure into data.
API_BLOCKED_RETRIES = 2


def _credit_cost(r: requests.Response) -> int:
    """Credits ScraperAPI billed for a response (its sa-credit-cost header); 1 if absent."""
    try:
        return max(1, int(r.headers.get("sa-credit-cost") or 1))
    except ValueError:
        return 1


def fetch_scraperapi(url: str, api_key: str, render: bool = False,
                     country: str | None = None,
                     on_credit: Callable[[int], object] | None = None) -> str:
    """Fetch via ScraperAPI (rotating proxy pool) — for Cloudflare-guarded
    aggregators that challenge datacenter IPs (CI).

    A call does not cost a flat 1 credit. ScraperAPI's rates, confirmed against the
    account counter on 2026-11-23: 10 for a page behind bot protection (salidzini,
    ceno — Cloudflare), 5 for Amazon, 1 for an ordinary site; render=true adds 10.
    Every response states its own figure in the `sa-credit-cost` header, and
    `on_credit` receives it once per billed (2xx) response — so the caller's usage
    counter counts credits, not calls. A challenge page returned as HTTP 200 is
    billed too, which is why those get API_BLOCKED_RETRIES more tries on a fresh
    exit (plain fetches only: a rendered one that still shows a challenge after its
    wait is not worth another 10+).

    `country` pins the exit country at no extra cost (verified 2026-11-19:
    country_code=lv gets salidzini past its Turnstile). Rendered fetches add
    wait=8000 so CF managed challenges have time to resolve. Network errors and
    ScraperAPI's own 5xx are retried once (unbilled); 4xx raise immediately.
    """
    params: dict = {"api_key": api_key, "url": url}
    if render:
        params["render"] = "true"
        params["wait"] = "8000"
    if country:
        params["country_code"] = country
    timeout = 100 if render else 60
    last: FetchError | None = None
    hard_retries = 1             # network errors / ScraperAPI's own 5xx — as before
    blocked_retries = 0 if render else API_BLOCKED_RETRIES
    while True:
        try:
            r = _session.get("https://api.scraperapi.com/", params=params, timeout=timeout)
        except requests.RequestException as e:
            last = FetchError(f"scraperapi network error: {e}")
            if not hard_retries:
                raise last from e
            hard_retries -= 1
            time.sleep(5)
            continue
        _force_utf8(r)
        if r.status_code < 300 and on_credit:
            on_credit(_credit_cost(r))
        if r.status_code == 200 and not _looks_blocked(r.text):
            return r.text
        blocked = " (anti-bot page)" if _looks_blocked(r.text) else ""
        last = FetchError(f"scraperapi HTTP {r.status_code}{blocked}", r.status_code)
        if r.status_code == 200 and blocked and blocked_retries:
            blocked_retries -= 1        # flagged exit — the pool rotates, ask again
            log.info("scraperapi: %s got a challenge page from a flagged exit — retrying",
                     urlparse(url).netloc)
            time.sleep(3)
            continue
        if r.status_code >= 500 and hard_retries:
            hard_retries -= 1
            time.sleep(10)
            continue
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


def _control_label(el) -> str:
    """A form control's label: inner text for buttons/links, value for <input type=submit>."""
    try:
        return (el.evaluate("e => (e.innerText || '') + ' ' + (e.value || '') + ' ' "
                            "+ (e.getAttribute('aria-label') || '')") or "").strip().lower()
    except Exception:  # noqa: BLE001 — a detached node is simply not our control
        return ""


def _dismiss_bot_wall(page) -> bool:
    """Amazon serves a clickable bot-check interstitial — click through it if present.

    Click the control whose own text matches the wall ("Continue shopping" etc.);
    the first <a> on such pages is usually the logo and leads nowhere useful.
    Returns True if a wall control was clicked — since 2026-11 Amazon.pl's wall
    button lands on the storefront root instead of the deep link, so the caller
    must re-navigate to the requested URL.
    """
    try:
        body = (page.inner_text("body") or "").lower()[:2000]
        if not any(k in body for k in BOT_WALL_PHRASES):
            return False
        for sel in ("input[type=submit]", "button", "a"):
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                txt = _control_label(el)
                if txt and any(p in txt for p in BOT_WALL_PHRASES):
                    el.click(timeout=3000)
                    page.wait_for_load_state("domcontentloaded")
                    return True
        # no labeled control found — old fallback: first submit control
        for sel in ("input[type=submit]", "button", "a"):
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                return True
    except Exception:  # noqa: BLE001 — best effort; never break the fetch
        pass
    return False


def _landed_on(page) -> str:
    """Error suffix: where the page ended up. A wait that runs out on the storefront
    root is a wall click's redirect; on the requested URL, an unrecognised block page."""
    try:
        return f" [landed on {page.url[:120]} — {page.title()[:60]!r}]"
    except Exception:  # noqa: BLE001 — diagnostics must not mask the real error
        return ""


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
                    page: Any = None
                    try:
                        page = ctx.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                        title = (page.title() or "").lower()
                        if any(t in title for t in CAPTCHA_TITLES):
                            if not _wait_captcha_clear(page):
                                raise FetchError(  # deterministic block — retrying won't help
                                    f"captcha page (title={page.title()!r})")
                        if _dismiss_bot_wall(page):
                            # Amazon.pl's wall button redirects to the storefront
                            # root instead of the deep link (since 2026-11) — the
                            # click still sets the clearance cookie, so return to
                            # the requested URL before waiting.
                            page.goto(url, wait_until="domcontentloaded",
                                      timeout=35_000)
                        page.wait_for_selector(wait_selector, timeout=PLAYWRIGHT_WAIT_MS)
                        html = page.content()
                        if _looks_blocked(html):
                            raise FetchError("anti-bot page after render", 403)
                        return html
                    except FetchError as e:
                        last_err = e
                        if "captcha" in str(e):
                            raise           # don't retry hard captchas
                    except Exception as e:  # noqa: BLE001 — timeouts etc. get one retry
                        head, sep, tail = str(e).partition("\n")
                        last_err = FetchError(f"playwright {type(e).__name__}: {head}"
                                              f"{_landed_on(page)}{sep}{tail}")
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
