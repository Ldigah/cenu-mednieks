from bs4 import BeautifulSoup

import fx
import parse
import stores

AMAZON_HTML = """
<html><body>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price"><span class="a-offscreen">129,99\u00a0€</span></span>
  <span class="basisPrice"><span class="a-offscreen">159,00\u00a0€</span></span>
</div>
<div id="availability"><span>Auf Lager.</span></div>
</body></html>
"""

AMAZON_PLN_HTML = """
<html><body>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price"><span class="a-offscreen">399,00\u00a0zł</span></span>
</div>
<div id="availability"><span>Chwilowo niedostępny</span></div>
</body></html>
"""

AMAZON_REGEX_ONLY = """
<html><body><script>jQuery.parseJSON('{"priceAmount":149.95}');</script></body></html>
"""

JSONLD_HTML = """
<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"UGREEN LP923",
 "offers":{"@type":"Offer","price":"89.99","priceCurrency":"EUR",
 "availability":"https://schema.org/InStock"}}
</script>
<div class="price">999,00 €</div>
</body></html>
"""

JSONLD_MULTIPLE = """
<html><body>
<script type="application/ld+json">
{"@graph":[{"@type":"Offer","price":"120.00","priceCurrency":"EUR",
            "availability":"https://schema.org/InStock"},
           {"@type":"Offer","price":"99.50","priceCurrency":"EUR",
            "availability":"https://schema.org/InStock"}]}
</script>
</body></html>
"""

# Shape verified live on salidzini.lv 2026-11-18: AggregateOffer with an offers[]
# array; battery/accumulator offers must be filtered out before taking the min.
SALIDZINI_HTML = """
<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"JBL Flip 7",
 "offers":{"@type":"AggregateOffer","lowPrice":19.85,"offerCount":40,
  "offers":[
   {"@type":"Offer","price":"19.85","priceCurrency":"EUR",
    "category":"Mobilo talrunu baterijas","name":"Akumulators f. JBL Flip 4",
    "availability":"https://schema.org/InStock","seller":{"name":"lemona.lv"}},
   {"@type":"Offer","price":"79.5","priceCurrency":"EUR",
    "category":"Bluetooth skaļruņi","name":"Bluetooth skaļrunis JBL Flip 7",
    "availability":"https://schema.org/InStock","seller":{"name":"220.lv"}},
   {"@type":"Offer","price":"73","priceCurrency":"EUR",
    "category":"Bluetooth skaļruņi","name":"Bluetooth skaļrunis JBL Flip 7, black",
    "availability":"https://schema.org/InStock","seller":{"name":"24.lv"}}]}}
</script>
</body></html>
"""

SALIDZINI_CSS = """
<html><main><article>
<a class="shop" href="#"><strong>eltek.lv</strong></a>
<a class="product" href="/click.php?itemid=1"><h2>UGREEN LP923</h2><span>Turētāji</span>
<data>12,45\u00a0€</data></a>
</article></main></html>
"""


def test_amazon_css_extraction():
    adapter = stores.adapter_for("https://www.amazon.de/dp/B0TEST")
    ex = adapter.extract(AMAZON_HTML)
    assert ex is not None and ex.price == 129.99
    assert ex.old_price == 159.00
    assert ex.in_stock is True
    assert ex.display == "129,99\u00a0€"


def test_amazon_pln_converted_to_eur(monkeypatch):
    monkeypatch.setattr(fx, "rate_to_eur", lambda c: 0.25)
    adapter = stores.adapter_for("https://www.amazon.pl/dp/B0TEST")
    ex = adapter.extract(AMAZON_PLN_HTML)
    assert ex is not None and ex.price == 99.75            # 399.00 * 0.25
    assert ex.converted is True
    assert ex.display == "399,00\u00a0zł"
    assert ex.in_stock is False         # "Chwilowo niedostępny" must not match "dostępny"


def test_amazon_price_regex_fallback():
    adapter = stores.adapter_for("https://www.amazon.de/dp/B0TEST")
    ex = adapter.extract(AMAZON_REGEX_ONLY)
    assert ex is not None and ex.price == 149.95
    assert ex.source == "regex"


def test_jsonld_wins_over_css():
    adapter = stores.adapter_for("https://www.amazon.pl/dp/B0TEST")
    ex = adapter.extract(JSONLD_HTML)
    assert ex is not None and ex.source == "jsonld"
    assert ex.price == 89.99
    assert ex.in_stock is True
    assert ex.price != 999.00  # CSS price ignored when JSON-LD exists


def test_jsonld_multiple_offers_cheapest():
    soup = BeautifulSoup(JSONLD_MULTIPLE, "html.parser")
    ex = parse.jsonld_extraction(soup)
    assert ex is not None and ex.price == 99.50


def test_salidzini_aggregate_filters_accessories():
    adapter = stores.adapter_for("https://www.salidzini.lv/search?q=JBL+Flip+7")
    ex = adapter.extract(SALIDZINI_HTML)
    assert ex is not None and ex.price == 73.0            # not 19.85 (battery) and not lowPrice 19.85
    assert ex.shop == "24.lv"
    assert ex.offers == 2              # 2 real speaker offers after the filter
    assert ex.in_stock is True
    assert ex.source == "jsonld-aggregate"


def test_salidzini_css_fallback():
    adapter = stores.adapter_for("https://www.salidzini.lv/search?q=x")
    ex = adapter.extract(SALIDZINI_CSS)
    assert ex is not None and ex.price == 12.45
    assert ex.shop == "eltek.lv"


def test_availability_words():
    assert parse.availability_flag("https://schema.org/OutOfStock") is False
    assert parse.availability_flag("Auf Lager.") is True
    assert parse.availability_flag("Dostępny od razu") is True
    assert parse.availability_flag("whatever") is None
    assert parse.availability_flag(None) is None


def test_detect_currency():
    assert parse.detect_currency("399,00 zł") == "PLN"
    assert parse.detect_currency("12,99 €") == "EUR"


HINNAVAATLUS_HTML = """
<html><body><div class="offer"><span class="offer-price">89,65\u00a0€</span></div></body></html>
"""


def test_hinnavaatlus_first_offer_is_min():
    adapter = stores.adapter_for("https://www.hinnavaatlus.ee/5681989/jbl-flip-7-black/")
    ex = adapter.extract(HINNAVAATLUS_HTML)
    assert ex is not None and ex.price == 89.65
    assert ex.currency == "EUR"


def test_css_price_skips_empty_matches():
    # Amazon's corePriceDisplay offscreen span exists but is empty in raw HTML
    # (price injected client-side); the next matching span carries the real price.
    html = ('<div class="wrap"><span class="p"></span>'
            '<span class="p">12,30\u00a0€</span></div>')
    soup = BeautifulSoup(html, "html.parser")
    ex = parse.css_extraction(soup, {"price": ".p"})
    assert ex.price == 12.30
    assert ex.display is not None and "12,30" in ex.display
