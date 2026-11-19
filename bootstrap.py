"""Create the state gist from seed_products.json.

Usage (local):  python bootstrap.py     (needs GIST_TOKEN in .env — see SETUP.md)
If you skip this, the first CI run creates the gist automatically and prints GIST_ID.
"""
import sys

import state
from config import F_BOT, F_PRICES, F_PRODUCTS, GIST_TOKEN


def main() -> int:
    if not GIST_TOKEN:
        print("FATAL: GIST_TOKEN missing — put it in .env first (see SETUP.md)", file=sys.stderr)
        return 1
    files = {
        F_PRODUCTS: state.seed_products(),
        F_PRICES: {},
        F_BOT: {"update_offset": 0},
    }
    gist_id = state.create_gist(GIST_TOKEN, files)
    print("\n✅ State gist created.")
    print(f"GIST_ID={gist_id}")
    print("→ Add it as a GitHub Actions secret named GIST_ID (and to your local .env).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
