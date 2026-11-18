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


def fetch_requests(url: str) -> str:
    """Plain HTTP GET with retries; raises FetchError on failure/block."""
    last = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            r = _session.get(url, headers={**BASE_HEADERS, "Accept-Language": _lang_for(url)},
                             timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last = FetchError(f"network error: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200 and not _looks_blocked(r.text):
            return r.text
        blocked = " (anti-bot page)" if _looks_blocked(r.text) else ""
        last = FetchError(f"HTTP {r.status_code}{blocked}", r.status_code)
        # Cloudflare throttling (pigu.lt) needs a long cooldown, not 2 s
        time.sleep(45 if r.status_code in (403, 429) else 2 * (attempt + 1))
    raise last


BOT_WALL_PHRASES = ("weiter shoppen", "kontynuuj zakupy", "continue shopping",
                    "kliknij poniższy przycisk", "klicke auf die schaltfläche")

CAPTCHA_TITLES = ("robot check", "attention required", "just a moment", "access denied")


def _dismiss_bot_wall(page) -> None:
    """Amazon serves a clickable bot-check interstitial — click through it if present."""
    try:
        body = (page.inner_text("body") or "").lower()[:2000]
        if not any(k in body for k in BOT_WALL_PHRASES):
            return
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
                ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                          viewport={"width": 1366, "height": 900})
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                title = (page.title() or "").lower()
                if any(t in title for t in CAPTCHA_TITLES):
                    raise FetchError(f"captcha page (title={page.title()!r})")
                _dismiss_bot_wall(page)
                page.wait_for_selector(wait_selector, timeout=20_000)
                html = page.content()
                if _looks_blocked(html):
                    raise FetchError("anti-bot page after render", 403)
                return html
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001 — Playwright raises its own error types
        raise FetchError(f"playwright {type(e).__name__}: {e}") from e
