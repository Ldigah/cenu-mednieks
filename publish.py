"""Static web snapshot / Telegram Mini App v2 (GitHub Pages).

Writes to --out (site/ in CI):
  - index.html / style.css / app.js  ← copied from app/ (light-JS Mini App, Q11)
  - data.json                        ← public catalog+stats (NO user data, Q33)
  - charts/<pid>.png                 ← 90-day best-price charts (Q14)
  - index.html.legacy removed        ← the zero-JS page was replaced by the app

Personal state (watchlists/targets) never lands here — the app fetches it live
from the Worker /api/* endpoints, authenticated with Telegram initData (Q10).

Run in CI after the tracker step:  python publish.py --out site
Usage:
  python publish.py --out site        # from the gist (uses GIST_TOKEN/GIST_ID)
  python publish.py --out site --local  # from state-local/ (dev)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import catalog
import state as state_mod

APP_DIR = Path(__file__).parent / "app"
DEFAULT_API_BASE = "https://cenu-mednieks-bot.ldigah.workers.dev"
DEFAULT_BOT_USERNAME = "cenu_mednieks_bot"


def build_data(products: dict, prices: dict) -> dict:
    """Public data.json — catalog + derived stats only; never any user data (Q33)."""
    now = datetime.now(UTC)
    out_products = []
    for prod in products.get("products", []):
        s = catalog.product_stats(prod, prices)
        best = s.get("best") or {}
        atl = prod.get("atl") or {}
        above_atl = None
        if best.get("price") is not None and atl.get("price") not in (None, 0):
            if best["price"] <= atl["price"]:
                above_atl = 0
            else:
                above_atl = round((best["price"] - atl["price"]) / atl["price"] * 100)
        offer_rows = []
        for o in prod.get("offers") or []:
            st = prices.get(o["url"], {}) or {}
            offer_rows.append({"url": o["url"], "store": o["store"],
                               "label": o.get("label") or o["store"],
                               "price": st.get("price"), "old_price": st.get("old_price"),
                               "in_stock": st.get("in_stock"), "fails": st.get("fails") or 0,
                               "shop": st.get("shop")})
        out_products.append({"id": prod["id"], "name": prod.get("name") or prod["id"],
                             "image": prod.get("image"), "tags": prod.get("tags") or [],
                             "threshold_pct": prod.get("threshold_pct"),
                             "atl": atl or None, "stats": s,
                             "above_atl": above_atl, "offers": offer_rows,
                             "chart": f"charts/{prod['id']}.png"})
    return {"generated": now.isoformat(timespec="seconds"),
            "api_base": os.environ.get("APP_API_URL", DEFAULT_API_BASE),
            "bot": os.environ.get("TELEGRAM_BOT_USERNAME", DEFAULT_BOT_USERNAME),
            "tags": catalog.tag_index(products),
            "products": out_products}


def write_charts(products: dict, prices: dict, out: Path) -> int:
    """90-day best-price chart PNG per product (matplotlib, cached by Pages)."""
    import charts
    charts_dir = out / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for prod in products.get("products", []):
        try:
            png = charts.chart_product(prod.get("name") or prod["id"],
                                       prod.get("offers") or [], prices,
                                       prod.get("atl"))
        except Exception as e:  # noqa: BLE001 — one bad chart never kills publish
            print(f"chart {prod['id']} failed: {e}")
            continue
        if png:
            (charts_dir / f"{prod['id']}.png").write_bytes(png)
            written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the Mini App snapshot")
    ap.add_argument("--out", default="site", help="output directory")
    ap.add_argument("--local", action="store_true", help="read state-local/ instead of the gist")
    args = ap.parse_args()

    store = state_mod.default_store(args.local)
    products = catalog.migrate_products(store.read("products.json") or {"products": []})
    prices = store.read("prices.json") or {}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if APP_DIR.is_dir():
        for src in APP_DIR.iterdir():
            if src.is_file():
                shutil.copy2(src, out / src.name)

    data = build_data(products, prices)
    (out / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n_charts = write_charts(products, prices, out)

    # stale charts for removed products linger harmlessly; clean ones with no product
    keep = {f"charts/{p['id']}.png" for p in data["products"]}
    charts_dir = out / "charts"
    if charts_dir.is_dir():
        for f in charts_dir.iterdir():
            if f.is_file() and f"charts/{f.name}" not in keep:
                f.unlink()

    print(f"site written to {out}/ ({len(data['products'])} products, {n_charts} charts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
