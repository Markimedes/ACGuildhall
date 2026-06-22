"""Characterization tests for build_ah_view -- the inventory classification logic
(region assignment, stack aggregation, and the auctionable / vendor-priced /
soulbound / not-priced decision) that Phase 3 will move into the auction
blueprint. The three external lookups it calls are faked.
"""

from __future__ import annotations

import ahprices
import ahservice
import db
from app import build_ah_view

# Inventory slots: 23 = backpack (inventory region), 39 = main bank (bank region).
_BACKPACK = 23
_BANK = 39


def _row(item_guid, entry, slot, *, count=1, flags=0, random=0, bag=0):
    return {
        "item_guid": item_guid, "itemEntry": entry, "slot": slot, "bag": bag,
        "count": count, "flags": flags, "randomPropertyId": random,
    }


def _patch_lookups(monkeypatch, sell_prices, prices):
    monkeypatch.setattr(ahprices, "display_for", lambda entries: {
        e: {"n": f"Item{e}", "icon": "ico", "q": 2, "ilvl": 10} for e in entries
    })
    monkeypatch.setattr(db, "item_sell_prices", lambda entries: sell_prices)
    # prices: {entry: (buy, bid, mult) or None}
    monkeypatch.setattr(ahservice, "price",
                        lambda entry, random_id, ilvl, quality, side: prices.get(entry))


def _by_entry(section):
    return {it["entry"]: it for it in section}


def test_build_ah_view_classifies_items(monkeypatch):
    rows = [
        _row(1, 100, _BACKPACK, count=5),            # auctionable (buy > sell)
        _row(2, 101, _BACKPACK + 1, flags=0x1),      # soulbound -> vendor
        _row(3, 102, _BANK, count=2),                # bank, buy <= sell -> vendor
        _row(4, 103, _BACKPACK + 2),                 # service has no price
    ]
    _patch_lookups(
        monkeypatch,
        sell_prices={100: 50, 101: 30, 102: 40, 103: 0},
        prices={100: (200, 180, 1.0), 102: (10, 9, 1.0), 103: None},
    )
    sections, totals = build_ah_view(rows)
    inv = _by_entry(sections["inventory"])
    bank = _by_entry(sections["bank"])

    # 100: real auction market item, priced at the bot's buy price.
    assert inv[100]["sellable"] and not inv[100]["vendor_priced"]
    assert inv[100]["unit"] == 200 and inv[100]["total"] == 1000

    # 101: soulbound with a vendor price -> instant-sell at the vendor price.
    assert inv[101]["vendor_priced"] and inv[101]["soulbound"]
    assert inv[101]["unit"] == 30 and inv[101]["total"] == 30

    # 102: market won't beat the vendor -> vendor-priced, and it's in the bank.
    assert bank[102]["vendor_priced"]
    assert bank[102]["unit"] == 40 and bank[102]["total"] == 80

    # 103: not covered by the pricing service -> not sellable, no price.
    assert not inv[103]["sellable"] and inv[103]["unit"] is None

    # Totals count sellable items only (103 contributes nothing).
    assert totals["inventory"] == 1000 + 30
    assert totals["bank"] == 80


def test_build_ah_view_aggregates_stacks(monkeypatch):
    # Two stacks of the same entry in the backpack collapse into one row whose
    # count is the sum, carrying both underlying guids.
    rows = [
        _row(10, 200, _BACKPACK, count=3),
        _row(11, 200, _BACKPACK + 1, count=2),
    ]
    _patch_lookups(
        monkeypatch,
        sell_prices={200: 5},
        prices={200: (100, 90, 1.0)},
    )
    sections, _ = build_ah_view(rows)
    inv = sections["inventory"]
    assert len(inv) == 1
    assert inv[0]["count"] == 5
    assert inv[0]["guids"] == [10, 11]
