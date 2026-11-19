"""Static web snapshot for GitHub Pages / the Telegram Mini App.

Reads the state (gist or local), renders one self-contained index.html
(inline CSS, inline SVG sparklines — no JS, no external assets) plus a
data.json for future client-side tricks. Run in CI after the tracker step:

    python publish.py --out site

Usage:
  python publish.py --out site        # from the gist (uses GIST_TOKEN/GIST_ID)
  python site.py --out site --local  # from state-local/ (dev)
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import catalog
import state as state_mod


def _sparkline(history: list[dict], w: int = 240, h: int = 48) -> str:
    """Inline SVG polyline of the last ~30 days of the product's best price."""
    raw: list[tuple[str, float]] = []
    for point in history:
        pv = point.get("p")
        if pv is not None:
            raw.append((str(point.get("t")), float(pv)))
    pts = raw[-90:]
    if len(pts) < 2:
        return ""
    try:
        parsed = [(datetime.fromisoformat(t).timestamp(), p) for t, p in pts]
    except (ValueError, TypeError):
        return ""
    ys = [p for _, p in parsed]
    xs = [x for x, _ in parsed]
    lo, hi = min(ys), max(ys)
    x0, x1 = min(xs), max(xs)
    span_x = max(x1 - x0, 1)
    span_y = max(hi - lo, 0.01)
    coords = [
        (2 + (x - x0) / span_x * (w - 4), h - 4 - (y - lo) / span_y * (h - 8))
        for x, y in parsed
    ]
    poly = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="price trend">'
            f'<polyline points="{poly}" fill="none" stroke="#2f81f7" stroke-width="2"/>'
            f'</svg>')


def render_html(products: dict, prices: dict, generated_at: datetime) -> str:
    cards = []
    for prod in products.get("products", []):
        s = catalog.product_stats(prod, prices)
        b = s["best"]
        offer_rows = []
        for o in prod.get("offers") or []:
            st = prices.get(o["url"], {}) or {}
            if st.get("fails"):
                cell = f'<span class="warn">failing ×{st["fails"]}</span>'
            elif st.get("price") is not None:
                shop = f' ({html.escape(str(st["shop"]))})' if st.get("shop") else ""
                out = ' <span class="out">out</span>' if st.get("in_stock") is False else ""
                cell = f'<b>{st["price"]:.2f} €</b>{shop}{out}'
            else:
                cell = '<span class="muted">not checked yet</span>'
            offer_rows.append(f'<tr><td><a href="{html.escape(o["url"])}">'
                              f'{html.escape(o["store"])}</a></td><td>{cell}</td></tr>')
        best_line = "no data yet"
        if b["price"] is not None:
            best_line = (f'<span class="big">{b["price"]:.2f} €</span> '
                         f'<span class="store">at {html.escape(str(b["store"]))}</span>')
        atl = s["atl"]
        atl_line = ""
        if atl and atl.get("price") is not None:
            d = html.escape(str(atl.get("date", ""))[:10])
            atl_line = (f'<div class="atl">🏆 all-time low {atl["price"]:.2f} € '
                        f'<span class="muted">({d})</span></div>')
        hist: list[dict] = []
        if b["url"]:
            hist = (prices.get(b["url"], {}) or {}).get("history") or []
        cards.append(f"""<section class="card">
  <h2><a href="{html.escape(str(b['url']) or '#')}">{html.escape(prod['name'] or prod['id'])}</a></h2>
  <div class="best">{best_line}</div>
  {atl_line}
  {_sparkline(hist)}
  <details><summary>{len(prod['offers'])} stores</summary>
  <table>{''.join(offer_rows)}</table></details>
</section>""")

    generated = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cenu Mednieks — price tracker</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #0d1117; color: #e6edf3;
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
header {{ padding: 20px 16px 6px; max-width: 720px; margin: 0 auto; }}
header h1 {{ font-size: 1.35rem; margin: 0; }}
header p {{ color: #8b949e; margin: 4px 0 0; font-size: .85rem; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px;
        padding: 14px 16px; margin: 12px auto; max-width: 720px; }}
.card h2 {{ font-size: 1.05rem; margin: 0 0 6px; }}
.card h2 a {{ color: #e6edf3; text-decoration: none; }}
.big {{ font-size: 1.5rem; font-weight: 700; color: #3fb950; }}
.store {{ color: #8b949e; }}
.atl {{ color: #e3b341; font-size: .9rem; margin: 4px 0; }}
.spark {{ margin: 8px 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; margin-top: 6px; }}
td {{ padding: 4px 6px; border-top: 1px solid #21262d; }}
td a {{ color: #2f81f7; text-decoration: none; }}
summary {{ cursor: pointer; color: #8b949e; font-size: .9rem; }}
.warn {{ color: #f85149; }} .out {{ color: #f85149; font-size: .8rem; }}
.muted {{ color: #8b949e; }}
footer {{ text-align: center; color: #8b949e; font-size: .8rem; padding: 18px 0 30px; }}
</style></head><body>
<header><h1>🛒 Cenu Mednieks</h1>
<p>Best prices across Latvian, Baltic and EU shops — refreshed by
<a style="color:#2f81f7" href="https://github.com/Ldigah/cenu-mednieks">GitHub Actions</a> 3&times;/day.
</p></header>
{''.join(cards) or '<p style="text-align:center">Nothing tracked yet.</p>'}
<footer>Generated {generated} · data: private gist · alerts: Telegram bot</footer>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the static price snapshot")
    ap.add_argument("--out", default="site", help="output directory")
    ap.add_argument("--local", action="store_true", help="read state-local/ instead of the gist")
    args = ap.parse_args()

    store = state_mod.default_store(args.local)
    products = catalog.migrate_products(store.read("products.json") or {"products": []})
    prices = store.read("prices.json") or {}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    (out / "index.html").write_text(render_html(products, prices, now), encoding="utf-8")
    data = [{"id": p["id"], "name": p["name"], "atl": p.get("atl"),
             "stats": catalog.product_stats(p, prices), "offers": p["offers"]}
            for p in products.get("products", [])]
    (out / "data.json").write_text(
        json.dumps({"generated": now.isoformat(timespec="seconds"), "products": data},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"site written to {out}/ ({len(data)} products)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
