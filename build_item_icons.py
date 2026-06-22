#!/usr/bin/env python3
"""Build the static item-icon map (item_icons.json) used by the Auction House tab.

Item icons live only in the client DBCs (ItemDisplayInfo.dbc), not the DB, and
they never change for a fixed 3.3.5a client -- so unlike prices they're baked once
into a small ``{displayid: icon}`` map rather than read live. The live pricing path
(ahprices.py) joins item_template.displayid against this map.

Run once (only re-run if the client DBCs ever change):
    python build_item_icons.py [--dbc-dir PATH] [--out item_icons.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_recipes import discover_dbc_dir, parse_item_display_icons


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbc-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("item_icons.json"))
    args = ap.parse_args()

    dbc_dir = args.dbc_dir or discover_dbc_dir()
    print(f"Reading {dbc_dir}/ItemDisplayInfo.dbc")
    icons = parse_item_display_icons(dbc_dir / "ItemDisplayInfo.dbc")
    # Drop empties to keep the file small; missing -> questionmark at render time.
    icons = {str(k): v for k, v in icons.items() if v}
    args.out.write_text(json.dumps(icons, separators=(",", ":")), encoding="utf-8")
    kb = args.out.stat().st_size / 1024
    print(f"Wrote {len(icons)} display icons to {args.out} ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
