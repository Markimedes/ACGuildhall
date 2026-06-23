"""Item display info for the Auction House tab.

Prices come entirely from the AHPricingService (see ``ahservice.py``); this module
only supplies the name, quality and icon needed to render each item, plus the item
level passed to the service so it can value a rolled random property/suffix.

Icons are static (they live in the client DBCs and never change), loaded once from
``item_icons.json``; names/quality/item level are read live from
``acore_world.item_template``.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import db

_HERE = Path(__file__).resolve().parent

# Static icon map: displayid -> icon basename (built once by build_item_icons.py).
try:
    _ICONS: dict[int, str] = {
        int(k): v for k, v in
        json.loads((_HERE / "item_icons.json").read_text(encoding="utf-8")).items()
    }
except FileNotFoundError:
    _ICONS = {}


def display_for(entries) -> dict[int, dict]:
    """Return ``{entry: {"n","q","icon","ilvl"}}`` for the given item entries
    (omitting any without an item_template row)."""
    entries = {int(e) for e in entries if e}
    if not entries:
        return {}
    out: dict[int, dict] = {}
    for it in db.item_display_for(entries):
        out[it["entry"]] = {
            "n": it["name"],
            "q": it["Quality"],
            "icon": _ICONS.get(it["displayid"], ""),
            "ilvl": it["ItemLevel"],
        }
    return out
