"""kurpirkt.lv adapter: /cena.php?q= search page IS the offers list (verified 2026-11-18)."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

import parse
from stores.base import StoreAdapter

# kurpirkt.lv accessory polluters (verified live: cases, bags, chargers, wall mounts
# undercut the real product). NEVER add turētājs/stiprinājums — LP923 IS a holder.
KURPIKT_EXCLUDE_RE = re.compile(
    r"(?i)maci|futrāl|futeral|som[aiu]\b|apvalk|vāciņ|vacin|korpus|etui|\bcase\b|\bcover\b"
    r"|batter|bateri|akumulator|uzlādējam|lādētāj|sienas|griestu|\bwall\b")


class KurpirktAdapter(StoreAdapter):
    """kurpirkt.lv aggregator.

    - No JSON-LD; rows are schema.org microdata (.price > [itemprop=price], dot-decimal).
    - Each .precebloks row = one shop's offer; sponsored rows use .campaignprice/.campaignname.
    - Keep rows whose a[title] contains every query token (h1 itemprop=name), drop the
      accessory regex, take min. '#head_resultcount' AggregateOffer is stale/polluted.
    - Optional &p1=<floor> URL param (the site's own price filter) keeps real offers on
      page 1 — keep it BELOW any plausible sale price (p1=90 hid the 65 € Flip 7 min).
    - Cloudflare managed challenge on every plain-requests fetch (home IP incl. full
      Chrome headers) → Playwright only; challenge usually auto-clears ≤10 s.
    """

    def extract(self, html: str) -> parse.Extraction | None:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.select_one('h1[itemprop="name"]')
        terms = re.findall(r"[a-z0-9]+", h1.get_text(" ", strip=True).lower()) if h1 else []
        best = None
        best_price: float | None = None
        for blk in soup.select(".precebloks"):
            a = blk.select_one("a[title]")
            name = a.get("title", "") if a else ""
            low = name.lower()
            if terms and not all(t in low for t in terms):
                continue
            if KURPIKT_EXCLUDE_RE.search(low):
                continue
            pe = blk.select_one(".price") or blk.select_one(".campaignprice")
            price = parse.parse_price(pe.get_text(" ", strip=True)) if pe else None
            if price is None:
                continue
            se = blk.select_one(".shop .name a") or blk.select_one(".campaignname a")
            stock = blk.select_one(".stock")
            stock_txt = stock.get_text(" ", strip=True) if stock else None
            cand = parse.Extraction(
                price=price, currency="EUR",
                in_stock=None if stock_txt is None
                else stock_txt.rsplit(":", 1)[-1].strip().lower() != "nav",
                shop=se.get_text(" ", strip=True) if se else None,
                offers=None, source="css-aggregate")
            if best_price is None or price < best_price:
                best = cand
                best_price = price
        return self.finalize(best)


KURPIKT = KurpirktAdapter(
    domain="kurpirkt.lv",
    label="kurpirkt.lv (all LV shops)",
    strategy="playwright",   # CF managed challenge on ALL plain-requests fetches
    dc_blocked=True,         # GitHub runners get challenged → ScraperAPI (10 credits, rendered)
    render_api=True,         # remote API fetches need JS rendering to clear Cloudflare
    daily_only=True,         # rendered fetches cost 10 API credits — poll once per day
    selectors={  # documentation only — extract() above is the real path
        "price": ".price", "campaign_price": ".campaignprice",
        "cheapest_shop": ".shop .name a", "campaign_shop": ".campaignname a",
        "availability": ".stock",
    },
    wait_selector=".precebloks",
    notes="Aggregator (salidzini-style min across rows); prices dot-decimal, trailing '*' = "
          "loyalty price. /cena.php?q= URLs, optionally &p1=<floor> to skip the accessory "
          "band on page 1. Playwright auto-clears the CF challenge; a stuck challenge "
          "fails one poll and self-recovers.",
)
