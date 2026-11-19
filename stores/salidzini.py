"""salidzini.lv adapter: the /search?q= page IS the offers list (verified 2026-11-18)."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

import parse
from stores.base import EXCLUDE_CATEGORY_RE, StoreAdapter


class SalidziniAdapter(StoreAdapter):
    """salidzini.lv aggregator.

    - AggregateOffer.offers[] in JSON-LD: min() of prices (NOT lowPrice — unreliable),
      seller.name of the cheapest, len() (NOT offerCount — counts ad rows).
    - Battery/accessory offers are filtered out (they pollute fuzzy matches).
    - No old-price/discount data exists on this site at all.
    - Slug URLs (/jbl-flip-7) and the /cena?q= redirect serve hCaptcha to
      non-browser clients — only /search?q= URLs are safe with plain requests.
    """

    def extract(self, html: str) -> parse.Extraction | None:
        soup = BeautifulSoup(html, "html.parser")
        ex = self._aggregate(soup)
        if ex is None:
            ex = parse.css_extraction(soup, self.selectors)
        return self.finalize(ex)

    def _aggregate(self, soup) -> parse.Extraction | None:
        best = None
        best_price: float | None = None
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
                    if best_price is None or p < best_price:
                        best = cand
                        best_price = p
        return best

    @staticmethod
    def _polluted(offer: dict) -> bool:
        blob = f"{offer.get('category', '')} {offer.get('name', '')}"
        return bool(EXCLUDE_CATEGORY_RE.search(blob))


SALIDZINI = SalidziniAdapter(
    domain="salidzini.lv",
    label="salidzini.lv (all LV shops)",
    strategy="requests+playwright",   # /search works via requests usually; Playwright if Cloudflare bites
    dc_blocked=True,                  # GitHub runners get challenged → ScraperAPI (1 credit, plain)
    selectors={  # CSS fallback only — JSON-LD AggregateOffer above is primary
        "price": "article a.product > data",
        "old_price": None,
        "availability": None,
        "cheapest_shop": "article a.shop > strong",
    },
    wait_selector="article",
    notes="Use /search?q= URLs only (slug URLs → hCaptcha). Prices dot-decimal in "
          "JSON-LD, '73,00 €' in HTML. No discount data exists on this site.",
)
