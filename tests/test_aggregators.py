"""Fixtures modeled on live kurpirkt.lv / ceno.lv pages (verified 2026-11-18)."""
import stores

KURPIKT_HTML = """
<html><body>
<h1 itemprop="name">ugreen lp923</h1>

<div class="precebloks">
  <a title="Ugreen 80856 dokstacija"><div class="price">23.99 €</div>
  <span class="shop"><span class="name"><a>x.lv</a></span></span></a>
</div>

<div class="precebloks">
  <a title="Ugreen LP923 MagSafe magnētiskais auto turētājs"><div class="price">12.70 €</div>
  <span class="shop"><span class="name"><a>Lukass.lv</a></span></span>
  <div class="stock">Noliktavā: 10</div></a>
</div>

<div class="precebloks">
  <a title="Ugreen LP923 MagSafe magnētiskais auto turētājs"><div class="price">9.95 €</div>
  <span class="shop"><span class="name"><a>ITworkshop.lv</a></span></span>
  <div class="stock">Noliktavā: 99+</div></a>
</div>

<div class="precebloks">
  <a title="Akumulators f. Ugreen LP923"><div class="price">4.50 €</div>
  <span class="shop"><span class="name"><a>battery.lv</a></span></span></a>
</div>
</body></html>
"""

CENO_HTML = """
<html><body><ul id="results">
<li itemprop="offers" itemtype="https://schema.org/Offer">
  <meta itemprop="price" content="9.95"><meta itemprop="priceCurrency" content="EUR">
  <meta itemprop="availability" content="http://schema.org/InStock">
  <span itemprop="seller"><meta itemprop="name" content="itworkshop.lv"></span>
  <span itemprop="name">Ugreen LP923 MagSafe turētājs</span>
</li>
<li itemprop="offers" itemtype="https://schema.org/Offer">
  <meta itemprop="price" content="12.45"><meta itemprop="priceCurrency" content="EUR">
  <meta itemprop="availability" content="http://schema.org/InStock">
  <span itemprop="seller"><meta itemprop="name" content="220.lv"></span>
  <span itemprop="name">Ugreen LP923</span>
</li>
<li itemprop="offers" itemtype="https://schema.org/Offer">
  <meta itemprop="price" content="19.85"><meta itemprop="priceCurrency" content="EUR">
  <meta itemprop="category" content="Mobilo talrunu baterijas">
  <span itemprop="seller"><meta itemprop="name" content="lemona.lv"></span>
  <span itemprop="name">Akumulators JBL Flip 4</span>
</li>
</ul>
<div itemtype="https://schema.org/AggregateOffer"><meta itemprop="offerCount" content="14"></div>
</body></html>
"""


def test_kurpirkt_filters_and_min():
    adapter = stores.adapter_for("https://www.kurpirkt.lv/cena.php?q=ugreen+lp923")
    ex = adapter.extract(KURPIKT_HTML)
    assert ex.price == 9.95                     # not 4.50 (battery) nor 23.99 (dock, term filter)
    assert ex.shop == "ITworkshop.lv"
    assert ex.in_stock is True                  # "Noliktavā: 99+"


def test_kurpirkt_out_of_stock_token():
    html = KURPIKT_HTML.replace("Noliktavā: 99+", "Nav").replace("9.95 €", "3.00 €")
    ex = stores.adapter_for("https://www.kurpirkt.lv/cena.php?q=x").extract(html)
    assert ex.price == 3.00
    assert ex.in_stock is False                 # "Nav"


def test_ceno_microdata_min():
    adapter = stores.adapter_for("https://ceno.lv/search?q=UGREEN+LP923")
    ex = adapter.extract(CENO_HTML)
    assert ex.price == 9.95                     # not 19.85 (battery excluded)
    assert ex.shop == "itworkshop.lv"
    assert ex.in_stock is True
    assert ex.offers == 14                      # global offerCount meta
    assert ex.source == "microdata"
