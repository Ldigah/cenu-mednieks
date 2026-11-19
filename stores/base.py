"""StoreAdapter base: JSON-LD first, CSS fallback, price_regex last; EUR normalization."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

import fx
import parse

# Accessory polluters that undercut the real product price on aggregators (verified:
# "Akumulator(s) f. JBL …" batteries matched the "JBL Flip 7" query at 19.85 €).
# Shared by salidzini and ceno.
EXCLUDE_CATEGORY_RE = re.compile(r"(?i)baterij|akumulator")


@dataclass
class StoreAdapter:
    domain: str
    label: str
    strategy: str                       # "requests" | "playwright" | "requests+playwright" | "api"
    selectors: dict = field(default_factory=dict)
    wait_selector: str = "body"         # Playwright wait target
    notes: str = ""
    dc_blocked: bool = False            # Cloudflare-challenges datacenter IPs → needs ScraperAPI in CI
    render_api: bool = False            # remote API fetches need JS rendering (credits ×10)
    daily_only: bool = False            # expensive fetches: poll once per day, not every run

    def extract(self, html: str) -> parse.Extraction | None:
        soup = BeautifulSoup(html, "html.parser")
        ex = parse.jsonld_extraction(soup)
        css = parse.css_extraction(soup, self.selectors)

        if css.price is not None and (ex is None or ex.price is None):
            ex = css
        elif ex is not None:
            # JSON-LD wins for price; enrich with CSS-only details
            if css.old_price is not None:
                ex.old_price = css.old_price
            if css.offers is not None:
                ex.offers = css.offers
            if css.shop is not None:
                ex.shop = css.shop
            if ex.in_stock is None:
                ex.in_stock = css.in_stock

        if (ex is None or ex.price is None) and self.selectors.get("price_regex"):
            m = re.search(self.selectors["price_regex"], str(soup))
            if m:
                ex = parse.Extraction(price=parse.parse_price(m.group(1)),
                                      currency="EUR", source="regex")

        return self.finalize(ex)

    def finalize(self, ex: parse.Extraction | None) -> parse.Extraction | None:
        """Convert non-EUR prices to EUR (Q23); display keeps the original text."""
        if ex and ex.price is not None and ex.currency and ex.currency != "EUR":
            rate = fx.rate_to_eur(ex.currency)
            if rate:
                ex.price = round(ex.price * rate, 2)
                if ex.old_price is not None:
                    ex.old_price = round(ex.old_price * rate, 2)
                ex.converted = True
        return ex

    def uses_playwright(self) -> bool:
        return self.strategy == "playwright"


def make_auto_adapter(host: str) -> StoreAdapter:
    """Generic JSON-LD adapter for shops added via /add that have no preset."""
    return StoreAdapter(domain=host, label=host, strategy="requests+playwright",
                        selectors={}, notes="auto (JSON-LD only)")
