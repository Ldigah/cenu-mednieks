"""Price-text parsing and HTML extraction (JSON-LD first, CSS selectors fallback)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

# Order matters: OUT first — "niedostępny" contains "dostępny", "nicht verfügbar" etc.
OUT_OF_STOCK_WORDS = ("out of stock", "outofstock", "sold out", "nicht verfügbar",
                      "nicht verfugbar", "derzeit nicht", "nav pieejams", "niedostępny",
                      "niedostepny", "chwilowo niedostępny", "unavailable", "preorder")
IN_STOCK_WORDS = ("in stock", "instock", "in_stock", "auf lager", "pieejam",
                  "dostępny", "dostepny", "available", "w magazynie", "limited availability")

CURRENCY_SYMBOLS = {"€": "EUR", "zł": "PLN", "£": "GBP", "Kč": "CZK"}


@dataclass
class Extraction:
    price: float | None = None
    old_price: float | None = None
    currency: str | None = None
    in_stock: bool | None = None
    shop: str | None = None      # cheapest offer's shop (aggregators)
    offers: int | None = None    # number of offers (aggregators)
    display: str | None = None   # raw price text as shown on the page
    converted: bool = False      # True if price was converted to EUR
    source: str = ""             # "jsonld" | "css" | "jsonld-aggregate" | "regex"


PRICE_RE = re.compile(r"\d[\d\s\u00a0.,]*")


def parse_price(text) -> float | None:
    """'1 299,99 €' / '12,99€' / '1299.99' / 1299 → float, else None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = PRICE_RE.search(str(text))
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace("\u00a0", "").rstrip(".,")
    if not re.search(r"\d", raw):
        return None
    if "," in raw and "." in raw:
        # Last separator is the decimal point; the other one is thousands.
        dec = raw[max(raw.rfind(","), raw.rfind("."))]
        thou = "," if dec == "." else "."
        raw = raw.replace(thou, "").replace(dec, ".")
    else:
        raw = raw.replace(",", ".")
    if raw.count(".") > 1:  # '1.299.99' → thousands dots
        head, tail = raw.rsplit(".", 1)
        raw = head.replace(".", "") + "." + tail
    try:
        return float(raw)
    except ValueError:
        return None


def detect_currency(text) -> str:
    for sym, cur in CURRENCY_SYMBOLS.items():
        if text and sym in str(text):
            return cur
    return "EUR"


def availability_flag(text) -> bool | None:
    if not text:
        return None
    t = str(text).lower()
    if any(w in t for w in OUT_OF_STOCK_WORDS):
        return False
    if any(w in t for w in IN_STOCK_WORDS):
        return True
    return None


def iter_nodes(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _is_offer(node: dict) -> bool:
    t = node.get("@type")
    if isinstance(t, str):
        return t == "Offer"
    if isinstance(t, list):
        return "Offer" in t
    return False


def jsonld_extraction(soup: BeautifulSoup) -> Extraction | None:
    """Cheapest price from schema.org JSON-LD — Product→offers or standalone Offer blocks."""
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
        for node in iter_nodes(data):
            offers = node.get("offers")
            if offers:
                offers_list = offers if isinstance(offers, list) else [offers]
            elif _is_offer(node):          # standalone Offer node (no Product parent)
                offers_list = [node]
            else:
                continue
            for off in offers_list:
                if not isinstance(off, dict):
                    continue
                price = off.get("price")
                if price is None and isinstance(off.get("priceSpecification"), dict):
                    price = off["priceSpecification"].get("price")
                p = parse_price(price)
                if p is None:
                    continue
                ex = Extraction(price=p, currency=off.get("priceCurrency") or "EUR",
                                in_stock=availability_flag(off.get("availability")))
                if best_price is None or p < best_price:
                    best = ex
                    best_price = p
    if best:
        best.source = "jsonld"
    return best


def css_extraction(soup: BeautifulSoup, selectors: dict) -> Extraction:
    """Extract using a store preset's CSS selectors (any of them may be missing)."""
    ex = Extraction(source="css")

    def txt(sel):
        if not sel:
            return None
        el = soup.select_one(sel)
        return el.get_text(" ", strip=True) if el else None

    price_txt = txt(selectors.get("price"))
    ex.price = parse_price(price_txt)
    if ex.price is not None:
        ex.display = price_txt
        ex.currency = detect_currency(price_txt)
    ex.old_price = parse_price(txt(selectors.get("old_price")))
    ex.in_stock = availability_flag(txt(selectors.get("availability")))
    ex.shop = txt(selectors.get("cheapest_shop")) or None

    offers_txt = txt(selectors.get("offers_count"))
    if offers_txt:
        m = re.search(r"\d+", offers_txt.replace(" ", "").replace("\u00a0", ""))
        ex.offers = int(m.group()) if m else None
    return ex
