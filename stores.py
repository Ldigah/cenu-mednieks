"""Store adapters: domain → extraction strategy.

Presets verified against live pages on 2026-11-18 by the selector-finder research
(salidzini.lv: high confidence; amazon.de/.pl: medium — see notes). The "*" adapter
auto-handles any other shop via JSON-LD. Extraction: JSON-LD first, CSS fallback,
then optional price_regex over inline scripts. Non-EUR prices convert to EUR (fx.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import fx
import parse

# salidzini.lv accessory polluters that undercut the real product price (verified:
# "Akumulator(s) f. JBL …" batteries matched the "JBL Flip 7" query at 19.85 €)
EXCLUDE_CATEGORY_RE = re.compile(r"(?i)baterij|akumulator")


@dataclass
class StoreAdapter:
    domain: str
    label: str
    strategy: str                       # "requests" | "playwright" | "requests+playwright"
    selectors: dict = field(default_factory=dict)
    wait_selector: str = "body"         # Playwright wait target
    notes: str = ""

    def extract(self, html: str) -> parse.Extraction:
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


class SalidziniAdapter(StoreAdapter):
    """salidzini.lv: the /search?q= page IS the offers list (verified 2026-11-18).

    - AggregateOffer.offers[] in JSON-LD: min() of prices (NOT lowPrice — unreliable),
      seller.name of the cheapest, len() (NOT offerCount — counts ad rows).
    - Battery/accessory offers are filtered out (they pollute fuzzy matches).
    - No old-price/discount data exists on this site at all.
    - Slug URLs (/jbl-flip-7) and the /cena?q= redirect serve hCaptcha to
      non-browser clients — only /search?q= URLs are safe with plain requests.
    """

    def extract(self, html: str) -> parse.Extraction:
        soup = BeautifulSoup(html, "html.parser")
        ex = self._aggregate(soup)
        if ex is None:
            ex = parse.css_extraction(soup, self.selectors)
        return self.finalize(ex)

    def _aggregate(self, soup) -> parse.Extraction | None:
        best = None
        for tag in soup.find_all("script", type="application/ld+json"):
            raw = tag.string or tag.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            for node in parse.iter_nodes(data):
                agg = node.get("offers")
                if not isinstance(agg, dict) or not isinstance(agg.get("offers"), list):
                    continue
                offers = agg["offers"]
                keep = [o for o in offers if isinstance(o, dict) and not self._polluted(o)]
                for off in keep:
                    p = parse.parse_price(off.get("price"))
                    if p is None:
                        continue
                    seller = off.get("seller")
                    shop = seller.get("name") if isinstance(seller, dict) else seller
                    cand = parse.Extraction(
                        price=p, currency="EUR",
                        in_stock=parse.availability_flag(off.get("availability")),
                        shop=shop, offers=len(keep), source="jsonld-aggregate")
                    if best is None or cand.price < best.price:
                        best = cand
        return best

    @staticmethod
    def _polluted(offer: dict) -> bool:
        blob = f"{offer.get('category', '')} {offer.get('name', '')}"
        return bool(EXCLUDE_CATEGORY_RE.search(blob))


STORES: dict[str, StoreAdapter] = {}


def register(adapter: StoreAdapter):
    STORES[adapter.domain] = adapter


def adapter_for(url: str) -> StoreAdapter:
    """Adapter for a URL; unknown domains get a generic JSON-LD "auto" adapter."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in STORES:
        return STORES[host]
    for domain, adapter in STORES.items():
        if host.endswith("." + domain):
            return adapter
    if not host:
        return STORES["*"]
    return StoreAdapter(domain=host, label=host, strategy="requests+playwright",
                        selectors={}, notes="auto (JSON-LD only)")


def supported_store_list() -> str:
    known = [a.label for d, a in sorted(STORES.items()) if d != "*"]
    return ", ".join(known) + " — plus any other shop with JSON-LD product data"


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

register(SalidziniAdapter(
    domain="salidzini.lv",
    label="salidzini.lv (all LV shops)",
    strategy="requests+playwright",   # /search works via requests usually; Playwright if Cloudflare bites
    selectors={  # CSS fallback only — JSON-LD AggregateOffer above is primary
        "price": "article a.product > data",
        "old_price": None,
        "availability": None,
        "cheapest_shop": "article a.shop > strong",
    },
    wait_selector="article",
    notes="Use /search?q= URLs only (slug URLs → hCaptcha). Prices dot-decimal in "
          "JSON-LD, '73,00 €' in HTML. No discount data exists on this site.",
))

register(StoreAdapter(
    domain="amazon.de",
    label="Amazon.de",
    strategy="playwright",   # plain requests = captcha even off datacenter IPs
    selectors={
        "price": ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, "
                  "#corePriceDisplay_mobile_feature_div .a-price .a-offscreen, "
                  "#price_inside_buybox, .buyingPrice .a-offscreen, .a-price .a-offscreen"),
        "old_price": ".basisPrice .a-offscreen, span.a-price.a-text-price .a-offscreen",
        "availability": "#availability",
        "price_regex": r'"priceAmount"\s*:\s*([0-9]+[.,][0-9]+|[0-9]+)',
    },
    wait_selector="#productTitle",
    notes="Buybox price; captcha on non-browser fetches → always Playwright. "
          "'Auf Lager' = in stock. No JSON-LD offers on dp pages.",
))

register(StoreAdapter(
    domain="amazon.pl",
    label="Amazon.pl",
    strategy="playwright",
    selectors={
        "price": ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, "
                  "#corePriceDisplay_mobile_feature_div .a-price .a-offscreen, "
                  "#price_inside_buybox, .buyingPrice .a-offscreen, .a-price .a-offscreen"),
        "old_price": ".basisPrice .a-offscreen, span.a-price.a-text-price .a-offscreen",
        "availability": "#availability",
        "price_regex": r'"priceAmount"\s*:\s*([0-9]+[.,][0-9]+|[0-9]+)',
    },
    wait_selector="#productTitle",
    notes="Prices in PLN (zł) → auto-converted to EUR via ECB rate. "
          "'Dostępny od razu'/'W magazynie' = in stock, 'Chwilowo niedostępny' = out.",
))

register(StoreAdapter(
    domain="pigu.lt",
    label="pigu.lt",
    strategy="requests+playwright",   # CF 403s datacenter IPs on requests; browser may pass
    selectors={},  # JSON-LD only by design: CSS price markup breaks (sup-concatenation → '11539')
    wait_selector="[class*='price'], .price",
    notes="Server-rendered; JSON-LD offers.price = REGULAR price (Pigu PLUS loyalty price sits "
          "in priceSpecification — not tracked). Bursts trigger a 403 'Ar jūs ne robotas' page: "
          "shared session + long backoff in fetch.py handle it. Same platform runs 220.lv / "
          "ksenukai.lt / kaup24.ee — the auto adapter covers those via JSON-LD.",
))

register(StoreAdapter(
    domain="hinnavaatlus.ee",
    label="hinnavaatlus.ee (EE shops)",
    strategy="requests",
    selectors={"price": ".offer-price"},
    notes="Static SSR, no JSON-LD. Offers are sorted by price, so the first .offer-price is "
          "the min (verified live 2026-11-18: 89.65 € min across 41 Flip 7 offers). "
          "A trailing '*' marks shop loyalty prices — ignored.",
))

# Generic fallback for any shop added via /add that has no preset above.
register(StoreAdapter(domain="*", label="auto (JSON-LD)", strategy="requests+playwright",
                      selectors={}, notes="tries JSON-LD only; add a preset if it fails"))
