"""Price-history charts for /chart — matplotlib, Agg backend, PNG bytes."""
from __future__ import annotations

import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

MAX_POINTS = 90  # ~30 days at 3 runs/day


def chart_product(product_name: str, entries: list[dict], prices: dict) -> bytes | None:
    """One PNG with a line per store entry for a single product. None if no history yet."""
    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    plotted = False
    for e in entries:
        st = prices.get(e["url"], {})
        hist = []
        for p in st.get("history", []):
            if p.get("p") is None:
                continue
            try:
                hist.append((datetime.fromisoformat(p["t"]), p["p"]))
            except ValueError:
                continue
        hist = hist[-MAX_POINTS:]
        if not hist:
            continue
        xs, ys = zip(*hist)
        ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.6,
                label=e.get("store_label", e["url"]))
        plotted = True
    if not plotted:
        plt.close(fig)
        return None
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


def chart_all(products: list[dict], prices: dict, limit: int = 4) -> list[tuple[str, bytes]]:
    """Charts grouped by product name — at most `limit` PNGs."""
    groups: dict[str, list[dict]] = {}
    for e in products:
        groups.setdefault(e["name"] or e["id"], []).append(e)
    out = []
    for name, entries in groups.items():
        if len(out) >= limit:
            break
        png = chart_product(name, entries, prices)
        if png:
            out.append((name, png))
    return out
