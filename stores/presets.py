"""Plain StoreAdapter presets: Amazon, pigu.lt, hinnavaatlus.ee, and the "*" auto adapter."""
from __future__ import annotations

from stores.base import StoreAdapter

AMAZON_DE = StoreAdapter(
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
)

AMAZON_PL = StoreAdapter(
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
)

PIGU = StoreAdapter(
    domain="pigu.lt",
    label="pigu.lt",
    strategy="requests+playwright",   # CF 403s datacenter IPs on requests; browser may pass
    selectors={},  # JSON-LD only by design: CSS price markup breaks (sup-concatenation → '11539')
    wait_selector="[class*='price'], .price",
    notes="Server-rendered; JSON-LD offers.price = REGULAR price (Pigu PLUS loyalty price sits "
          "in priceSpecification — not tracked). Bursts trigger a 403 'Ar jūs ne robotas' page: "
          "shared session + long backoff in fetch.py handle it. Same platform runs 220.lv / "
          "ksenukai.lt / kaup24.ee — the auto adapter covers those via JSON-LD.",
)

HINNAVAATLUS = StoreAdapter(
    domain="hinnavaatlus.ee",
    label="hinnavaatlus.ee (EE shops)",
    strategy="requests",
    selectors={"price": ".offer-price"},
    notes="Static SSR, no JSON-LD. Offers are sorted by price, so the first .offer-price is "
          "the min (verified live 2026-11-18: 89.65 € min across 41 Flip 7 offers). "
          "A trailing '*' marks shop loyalty prices — ignored.",
)

AUTO = StoreAdapter(domain="*", label="auto (JSON-LD)", strategy="requests+playwright",
                    selectors={}, notes="tries JSON-LD only; add a preset if it fails")
