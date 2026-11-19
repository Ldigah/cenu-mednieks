"""ceno.lv adapter: schema.org microdata, no Product JSON-LD (verified 2026-11-18)."""
from __future__ import annotations

from bs4 import BeautifulSoup

import parse
from stores.base import EXCLUDE_CATEGORY_RE, StoreAdapter


class CenoAdapter(StoreAdapter):
    """ceno.lv aggregator.

    - Prices are plain dot-decimal in meta[itemprop=price]@content.
    - min() over li[itemprop=offers] — NOT AggregateOffer lowPrice (off by a cent).
    - /search is globally price-sorted: page-1 min = overall min.
    - Cloudflare 403s non-browser UAs (empty body); browser UA + requests works from
      residential IPs — remote-API / playwright covers datacenter blocks.
    - No discount data exists on this site.
    """

    def extract(self, html: str) -> parse.Extraction | None:
        soup = BeautifulSoup(html, "html.parser")
        best, listed = None, 0
        best_price: float | None = None
        for li in soup.select('li[itemprop="offers"]'):
            if "Offer" not in (li.get("itemtype") or ""):
                continue
            pm = li.select_one('meta[itemprop="price"]')
            if pm is None:
                continue
            cat = li.select_one('meta[itemprop="category"]')
            name_el = li.select_one('span[itemprop="name"]')
            blob = ((cat.get("content") or "") if cat else "") + " " + \
                   (name_el.get_text(" ", strip=True) if name_el else "")
            if EXCLUDE_CATEGORY_RE.search(blob):
                continue
            p = parse.parse_price(pm.get("content"))
            if p is None:
                continue
            listed += 1
            seller = li.select_one('span[itemprop="seller"] meta[itemprop="name"]')
            avail = li.select_one('meta[itemprop="availability"]')
            in_stock = None
            if avail is not None:
                a = (avail.get("content") or "").lower()
                if "instock" in a:
                    in_stock = True
                elif "outofstock" in a or "soldout" in a:
                    in_stock = False
            cand = parse.Extraction(price=p, currency="EUR", in_stock=in_stock,
                                    shop=seller.get("content") if seller else None,
                                    source="microdata")
            if best_price is None or p < best_price:
                best = cand
                best_price = p
        if best is not None:
            cnt = soup.select_one('[itemtype*="AggregateOffer"] meta[itemprop="offerCount"]')
            c = cnt.get("content") if cnt else ""
            best.offers = int(c) if c.isdigit() else listed
        return self.finalize(best)


CENO = CenoAdapter(
    domain="ceno.lv",
    label="ceno.lv (LV shops)",
    strategy="requests+playwright",
    dc_blocked=True,         # datacenter IPs challenged even via Playwright → ScraperAPI (1 credit)
    selectors={"price": '#results li[itemprop="offers"] a[title^="Produkta cena"] span'},
    wait_selector='li[itemprop="offers"]',
    notes="Use /search?q= URLs (product slugs mostly soft-404). Browser UA mandatory "
          "(403 empty-body otherwise). offerCount meta = global offers; lowPrice meta "
          "can be off by a cent.",
)
