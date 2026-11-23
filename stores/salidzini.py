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

    def candidates(self, html: str, limit: int = 3) -> list[dict]:
        """Distinct product titles from the AggregateOffer rows of a /search page."""
        soup = BeautifulSoup(html, "html.parser")
        rows: list[tuple[str, float]] = []
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
                for off in agg["offers"]:
                    if not isinstance(off, dict) or self._polluted(off):
                        continue
                    p = parse.parse_price(off.get("price"))
                    name = off.get("name")
                    if p is not None and name:
                        rows.append((str(name), p))
        return self._group_rows(rows, limit)


SALIDZINI = SalidziniAdapter(
    domain="salidzini.lv",
    label="salidzini.lv (all LV shops)",
    strategy="requests+playwright",   # /search works via plain requests from residential IPs
    dc_blocked=True,                  # foreign datacenter IPs hit a Turnstile wall…
    api_country="lv",                 # …but an LV exit passes with PLAIN requests on ~3 of 4 calls
    render_api=False,
    daily_only=False,
    selectors={  # CSS fallback only — JSON-LD AggregateOffer above is primary
        "price": "article a.product > data",
        "old_price": None,
        "availability": None,
        "cheapest_shop": "article a.shop > strong",
    },
    wait_selector="article",
    notes="Use /search?q= URLs only (slug URLs → hCaptcha). Prices dot-decimal in "
          "JSON-LD, '73,00 €' in HTML. No discount data exists on this site. Verified "
          "2026-11-23 via ScraperAPI: plain + country_code=lv returns the full offers "
          "page on ~3 of 4 calls — the rest get its Turnstile page, which "
          "fetch_scraperapi retries; without the geo pin even renders hit Turnstile. "
          "10 credits a page (bot protection). Query hygiene: a query that resolves to a "
          "product-group page (brand + series, 'lenovo thinkpad t16 …') is hCaptcha-walled "
          "from every IP — drop the brand word. A search page is the ~40 cheapest matches, "
          "so a product with cheaper accessories needs a token only the real item has.",
)
