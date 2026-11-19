"""Price-history charts for /chart — matplotlib, Agg backend, PNG bytes.

Per store: faint lines. Bold black: the product's best price across stores over
time. Red dashed: the all-time low reference (when provided).
"""
from __future__ import annotations

import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

MAX_POINTS = 270  # ~90 days at 3 runs/day; old points beyond that are dropped


def _series(st: dict) -> list[tuple[datetime, float]]:
    hist = []
    for p in st.get("history", []):
        if p.get("p") is None:
            continue
        try:
            hist.append((datetime.fromisoformat(p["t"]), p["p"]))
        except ValueError:
            continue
    return hist[-MAX_POINTS:]


def chart_product(product_name: str, offers: list[dict], prices: dict,
                  atl: dict | None = None) -> bytes | None:
    """One PNG per product: per-store lines + bold best-price line (+ ATL marker).

    `offers` are the product's offer dicts (url + label). None if no history yet.
    """
    series: list[tuple[str, list[tuple[datetime, float]]]] = []
    for o in offers:
        s = _series(prices.get(o["url"], {}) or {})
        if s:
            series.append((o.get("label") or o.get("store") or o["url"], s))
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    for label, pts in series:
        xs, ys = zip(*pts, strict=False)
        ax.plot(xs, ys, marker="o", markersize=2.5, linewidth=1.1, alpha=0.55, label=label)

    # bold min-price line: at every timestamp, the cheapest last-known store price
    all_ts = sorted({t for _, pts in series for (t, _p) in pts})

    def last_before(pts: list[tuple[datetime, float]], t: datetime) -> float | None:
        cur = None
        for tt, pp in pts:
            if tt > t:
                break
            cur = pp
        return cur

    mx: list[datetime] = []
    my: list[float] = []
    for t in all_ts:
        vals = [v for v in (last_before(pts, t) for _, pts in series) if v is not None]
        if vals:
            mx.append(t)
            my.append(min(vals))
    if mx:
        ax.plot(mx, my, color="black", linewidth=2.2, label="best price")

    if atl and atl.get("price") is not None:
        ax.axhline(atl["price"], color="red", linestyle="--", linewidth=1.2,
                   label=f"all-time low {atl['price']:.2f} €")

    ax.set_title(product_name)
    ax.set_ylabel("€")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def chart_all(products: dict, prices: dict, limit: int = 4) -> list[tuple[str, bytes]]:
    """Charts per product — at most `limit` PNGs."""
    out: list[tuple[str, bytes]] = []
    for prod in products.get("products", []):
        if len(out) >= limit:
            break
        png = chart_product(prod["name"] or prod["id"], prod["offers"], prices, prod.get("atl"))
        if png:
            out.append((prod["name"] or prod["id"], png))
    return out
