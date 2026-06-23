"""Auction blueprint: the per-character sellable-inventory view, the review step,
and the list/instant-sell submission that drives the worldserver over SOAP. Also
holds the inventory classification (``build_ah_view``) and the worldserver-output
flash helpers. Mounted at /auctionhouse.
"""

from __future__ import annotations

import json

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from data import ahprices, ahservice, db, soap
from guildhall.core import active_character
from guildhall.extensions import limiter

bp = Blueprint("auction", __name__)

# Auction listing: valid durations (hours) and the deposit-estimate inputs. The
# worldserver computes the authoritative deposit; these only drive the preview and
# must match the realm's AuctionHouse.dbc deposit (5 = faction AH) and
# Rate.Auction.Deposit. See data/soap.py for the SOAP command channel.
AUCTION_DURATIONS = (12, 24, 48)

# Inventory slot ranges (from Player.h). Top-level rows have bag = 0; their slot
# says where they sit. Nested rows (bag != 0) live inside a container whose
# top-level slot decides whether it is an inventory bag or a bank bag.
_INV_BAG_SLOTS = range(19, 23)     # equipped inventory bag containers
_BACKPACK_SLOTS = range(23, 39)    # the default backpack
_BANK_SLOTS = range(39, 67)        # main bank item slots
_BANK_BAG_SLOTS = range(67, 74)    # equipped bank bag containers
ITEM_FLAG_SOULBOUND = 0x1
ITEM_QUALITY_POOR = 0          # grey "junk" items (the in-game "sell junk" set)


def build_ah_view(rows: list[dict]):
    """Group a character's held items into Inventory and Bank sections priced at
    the realm AH bot's estimate. Equipped gear and the bag containers themselves
    are skipped. Returns ``(sections, totals)`` where sections is
    ``{"inventory": [...], "bank": [...]}`` and totals is the sellable-only sum
    of each section in copper."""
    # Pass 1: which region each equipped container belongs to.
    container_region: dict[int, str] = {}
    for r in rows:
        if r["bag"] == 0:
            if r["slot"] in _INV_BAG_SLOTS:
                container_region[r["item_guid"]] = "inventory"
            elif r["slot"] in _BANK_BAG_SLOTS:
                container_region[r["item_guid"]] = "bank"

    # Pass 2: assign each holdable item to a region and aggregate stacks.
    # key = (region, entry, randomPropertyId, soulbound) -> count. Random property
    # is part of the key because the AHPricingService prices a rolled suffix
    # ("... of the Bear") differently from the bare item.
    agg: dict[tuple, int] = {}
    agg_guids: dict[tuple, list[int]] = {}  # same key -> underlying item-instance guids
    for r in rows:
        if r["bag"] == 0:
            if r["slot"] in _BACKPACK_SLOTS:
                region = "inventory"
            elif r["slot"] in _BANK_SLOTS:
                region = "bank"
            else:
                continue  # equipped gear or an equipped bag/bank-bag container
        else:
            region = container_region.get(r["bag"])
            if region is None:
                continue
        sb = bool(r["flags"] & ITEM_FLAG_SOULBOUND)
        key = (region, r["itemEntry"], int(r["randomPropertyId"]), sb)
        agg[key] = agg.get(key, 0) + int(r["count"])
        agg_guids.setdefault(key, []).append(r["item_guid"])

    sections = {"inventory": [], "bank": []}
    totals = {"inventory": 0, "bank": 0}
    # Display info (name/quality/icon/ilvl) for the held entries; market prices come
    # from the AHPricingService (per entry+random property), vendor SellPrice from
    # item_template. An item "sells at vendor price" -- marked **Vendor Priced** and
    # instant-sold rather than auctioned -- when either the market won't beat the
    # vendor (the bot's buy price is floored at SellPrice, so buy <= SellPrice) or
    # the item is SOULBOUND (can't be auctioned at all, so selling = vendoring).
    entries = {entry for (_, entry, _, _) in agg}
    display = ahprices.display_for(entries)
    sell_prices = db.item_sell_prices(entries)
    svc_cache: dict[tuple, tuple] = {}  # (entry, random) -> (buy, bid, mult)
    for (region, entry, random_id, sb), count in agg.items():
        d = display.get(entry)
        sp = int(sell_prices.get(entry, 0) or 0)        # vendor SellPrice
        ck = (entry, random_id)
        if ck not in svc_cache:
            # side="buy": what the bot PAYS for the item -- what the player gets
            # selling it. (The list/sell price would be misleading here.)
            res = ahservice.price(entry, random_id, d["ilvl"] if d else 0,
                                  d["q"] if d else 0, side="buy")
            svc_cache[ck] = res if res else (None, None, None)
        buy = svc_cache[ck][0]
        mult = svc_cache[ck][2]

        if sb:
            # Soulbound: can't be auctioned, only vendored -> worth the vendor price.
            if sp > 0:
                unit, total, sellable, reason, multiplier = sp, sp * count, True, None, None
                vendor_priced = True
            else:
                unit, total, sellable, reason, multiplier = None, None, False, "Soulbound", None
                vendor_priced = False
        elif buy is None:
            # service doesn't cover it / unreachable -> no price (not sellable here)
            unit, total, sellable, reason, multiplier = None, None, False, "Not priced", None
            vendor_priced = False
        elif sp > 0 and buy <= sp:
            # Market won't beat the vendor -> sells at the vendor price (instant-sell).
            unit, total, sellable, reason, multiplier = sp, sp * count, True, None, None
            vendor_priced = True
        else:
            # A real auctionable market item; the daily-event swing applies.
            unit, total, sellable, reason, multiplier = buy, buy * count, True, None, mult
            vendor_priced = False
        quality = d["q"] if d else 1
        sections[region].append({
            "entry": entry,
            "name": d["n"] if d else f"Item #{entry}",
            "icon": d["icon"] if d else "",
            "quality": quality,
            "count": count,
            "random": bool(random_id),
            "unit": unit,
            "multiplier": multiplier,
            "vendor_priced": vendor_priced,
            "soulbound": sb,
            # "Junk" = Poor (grey) quality -- the game's own junk classification,
            # matching the in-game "sell all junk". This is what the "Select junk"
            # button bulk-checks (greys are worthless regardless of binding).
            "junk": sellable and quality == ITEM_QUALITY_POOR,
            "total": total,
            "sellable": sellable,
            "reason": reason,
            # Underlying stacks (one auction each); only meaningful when sellable.
            "guids": sorted(agg_guids[(region, entry, random_id, sb)]),
        })
        if total is not None:
            totals[region] += total

    for lst in sections.values():
        # Sellable first (highest total), then the rest by name.
        lst.sort(key=lambda x: (x["total"] is None, -(x["total"] or 0), x["name"].lower()))
    return sections, totals


# ---------------------------------------------------------------------------
# Auction listing helpers
# ---------------------------------------------------------------------------
def _sellable_by_guid(char_guid: int) -> dict[int, dict]:
    """Index the character's current sellable items by item-instance guid, so a
    posted selection can be re-validated against live inventory."""
    sections, _ = build_ah_view(db.character_inventory_breakdown(char_guid))
    by_guid: dict[int, dict] = {}
    for region in ("inventory", "bank"):
        for it in sections[region]:
            if it["sellable"]:
                for g in it["guids"]:
                    by_guid[g] = it
    return by_guid


def _posted_guids(values) -> set[int]:
    """Parse checkbox values (each a CSV of item-instance guids) into a guid set."""
    out: set[int] = set()
    for raw in values:
        for part in str(raw).split(","):
            if part.isdigit():
                out.add(int(part))
    return out


def _to_gsc(copper) -> tuple[int, int, int]:
    copper = max(0, int(copper or 0))
    return copper // 10000, (copper // 100) % 100, copper % 100


def _flash_listing_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_* command output into a user flash."""
    if not ok:
        flash("Couldn't reach the game server to list your items. Try again "
              "shortly.", "error")
        return
    listed = failed = deposit = 0
    err = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_RESULT"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "listed" and v.isdigit():
                    listed = int(v)
                elif k == "failed" and v.isdigit():
                    failed = int(v)
                elif k == "deposit" and v.isdigit():
                    deposit = int(v)
        elif line.startswith("GUILDHALL_ERROR"):
            err = line[len("GUILDHALL_ERROR"):].strip()

    if err:
        msgs = {
            "character-online": "Log out of the game before listing items.",
            "no-such-character": "That character could not be found.",
        }
        # insufficient-funds need=.. have=..
        if err.startswith("insufficient-funds"):
            flash("Not enough gold to cover the auction deposit.", "error")
        else:
            flash(msgs.get(err, f"Listing failed: {err}"), "error")
        return
    if listed:
        gold = deposit // 10000
        msg = f"Posted {listed} auction{'s' if listed != 1 else ''}"
        if deposit:
            msg += f" (deposit {gold}g {deposit % 10000 // 100}s {deposit % 100}c)"
        if failed:
            msg += f"; {failed} item{'s' if failed != 1 else ''} could not be listed"
        flash(msg + ".", "ok" if not failed else "error")
    else:
        flash("Nothing could be listed (items may have moved, sold, or the stack "
              "size was invalid).", "error")


# Per-item rejection reasons the vendor command emits (GUILDHALL_FAIL reason=..),
# mapped to a player-facing explanation so a failed instant-sale says WHY.
_VENDOR_FAIL_REASONS = {
    "not-held": "the game server no longer has them (they may have moved or sold "
                "in-game since the page loaded)",
    "not-sellable": "they can't be sold this way (an item already in an auction, a "
                    "non-empty bag, or a time-limited item)",
    "no-vendor-price": "a vendor won't buy them",
}


def _flash_vendor_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_VENDORED output (the instant-sell path)
    into a user flash, surfacing the per-item rejection reason when items fail."""
    if not ok:
        flash("Couldn't reach the game server to sell your items. Try again "
              "shortly.", "error")
        return
    sold = failed = gained = 0
    saw_result = False
    reasons: list[str] = []
    err = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_VENDORED"):
            saw_result = True
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "sold" and v.isdigit():
                    sold = int(v)
                elif k == "failed" and v.isdigit():
                    failed = int(v)
                elif k == "gained" and v.isdigit():
                    gained = int(v)
        elif line.startswith("GUILDHALL_FAIL"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "reason":
                    reasons.append(v)
        elif line.startswith("GUILDHALL_ERROR"):
            err = line[len("GUILDHALL_ERROR"):].strip()

    if err:
        msgs = {"no-such-character": "That character could not be found."}
        flash(msgs.get(err, f"Instant sale failed: {err}"), "error")
        return
    if not saw_result:
        # No result line at all: the worldserver almost certainly doesn't have the
        # vendor command yet (it needs a rebuild + restart).
        flash("The instant-sell command isn't available on the game server yet "
              "-- it may need updating (rebuild + restart).", "error")
        return

    def _why() -> str:
        if not reasons:
            return "items may have moved or changed"
        top = max(set(reasons), key=reasons.count)
        return _VENDOR_FAIL_REASONS.get(top, top)

    if sold:
        g, s, c = gained // 10000, gained % 10000 // 100, gained % 100
        msg = (f"Instantly sold {sold} item{'s' if sold != 1 else ''} to the "
               f"vendor for {g}g {s}s {c}c")
        if failed:
            msg += f"; {failed} could not be sold ({_why()})"
        flash(msg + ".", "ok" if not failed else "error")
    elif failed:
        flash(f"Couldn't sell {failed} item{'s' if failed != 1 else ''} to the "
              f"vendor: {_why()}.", "error")
    else:
        flash("Nothing was sold to the vendor.", "error")


def _flash_refresh_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_SAVED output (the force-save behind the
    "Refresh inventory" button) into a user flash."""
    if not ok:
        flash("Couldn't reach the game server to refresh. Try again shortly.",
              "error")
        return
    online = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_SAVED"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "online" and v.isdigit():
                    online = int(v)
        elif line.startswith("GUILDHALL_ERROR"):
            flash("Couldn't refresh your inventory. Try again shortly.", "error")
            return
    if online:
        flash("Inventory refreshed from your online character.", "ok")
    elif online == 0:
        flash("Your character is offline, so the list is already current.", "ok")
    else:
        flash("Couldn't refresh your inventory. Try again shortly.", "error")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("")
@login_required
def index():
    ch = active_character()
    sections = {"inventory": [], "bank": []}
    totals = {"inventory": 0, "bank": 0}
    if ch:
        rows = db.character_inventory_breakdown(ch["guid"])
        sections, totals = build_ah_view(rows)
    return render_template(
        "auctionhouse.html",
        character=ch,
        sections=sections,
        totals=totals,
        grand_total=totals["inventory"] + totals["bank"],
        service_up=ahservice.available(),
        ah_events=ahservice.events(),
        listing_enabled=soap.enabled(),
        char_online=bool(ch and ch.get("online")),
        # "Refresh inventory" forces a server save; only useful when the
        # character is online and only when SOAP is wired up.
        can_refresh=bool(ch and soap.enabled()),
    )


# Rate-limit the force-save per character (configurable; default once a minute)
# so a user can't hammer the world thread. Only an actionable refresh (character
# present + SOAP up) counts; the no-character / SOAP-down cases are answered in
# the body with their own message and must not burn the per-character budget.
def _refresh_exempt():
    return not (active_character() and soap.enabled())


def _refresh_key():
    ch = active_character()
    return f"ahrefresh:{ch['guid']}" if ch else "ahrefresh:none"


def _refresh_breached(_limit):
    window = current_app.config["AH_REFRESH_SECONDS"]
    flash(f"Just refreshed -- please wait up to {window}s before "
          "refreshing again.", "error")
    return redirect(url_for("auction.index"))


@bp.route("/refresh", methods=["POST"])
@login_required
@limiter.limit(
    lambda: f"1 per {current_app.config['AH_REFRESH_SECONDS']} seconds",
    key_func=_refresh_key,
    exempt_when=_refresh_exempt,
    on_breach=_refresh_breached,
)
def refresh():
    ch = active_character()
    if not ch:
        return redirect(url_for("auction.index"))
    if not soap.enabled():
        flash("Refreshing isn't available right now.", "error")
        return redirect(url_for("auction.index"))
    ok, out = soap.command(f"guildhall save {ch['guid']}")
    _flash_refresh_result(ok, out)
    return redirect(url_for("auction.index"))


@bp.route("/review", methods=["POST"])
@login_required
def review():
    ch = active_character()
    if not ch:
        return redirect(url_for("auction.index"))
    if not soap.enabled():
        flash("Selling from the web isn't available right now.", "error")
        return redirect(url_for("auction.index"))

    selected = _posted_guids(request.form.getlist("row"))
    by_guid = _sellable_by_guid(ch["guid"])
    instances = db.held_item_instances(ch["guid"])
    # Group the still-valid selected stacks back into one editable line per
    # item (entry + rolled property), priced from the live inventory.
    groups: dict[tuple, dict] = {}
    for g in selected:
        it = by_guid.get(g)
        if not it or g not in instances:
            continue  # sold / moved / no longer sellable since the AH page
        # Group by item + whether it sells at vendor price, so a soulbound (or
        # otherwise vendor-priced) copy never merges with an auctionable one.
        key = (it["entry"], it["random"], it["vendor_priced"])
        grp = groups.setdefault(key, {
            "entry": it["entry"], "name": it["name"], "icon": it["icon"],
            "quality": it["quality"], "unit": it["unit"] or 0,
            "vendor_priced": it["vendor_priced"],
            "guids": [], "count": 0, "stacks": 0,
        })
        grp["guids"].append(g)
        grp["count"] += int(instances[g]["count"])
        grp["stacks"] += 1

    rows = sorted(groups.values(), key=lambda r: r["name"].lower())
    if not rows:
        flash("None of the selected items can be sold anymore.", "error")
        return redirect(url_for("auction.index"))

    entries = [r["entry"] for r in rows]
    sell_prices = db.item_sell_prices(entries)
    max_stacks = db.item_max_stacks(entries)
    # Vendor-priced items (the market won't beat the vendor, or they're
    # soulbound and can't be auctioned at all) are instant-sold to the vendor
    # on submit rather than auctioned. build_ah_view already decided this per
    # item; trust that flag here.
    auction_rows, vendor_rows = [], []
    for r in rows:
        r["guids_csv"] = ",".join(str(g) for g in r["guids"])
        r["guid_counts_csv"] = ",".join(
            str(instances[g]["count"]) for g in r["guids"])
        r["sell_price"] = sell_prices.get(r["entry"], 0)
        r["unit_price"] = r["unit"] or 0
        if r["vendor_priced"]:
            # Vendor (instant-sell) row: fixed price, not editable/auctioned.
            r["vendor_unit"] = r["sell_price"] or r["unit_price"]
            r["vendor_total"] = r["vendor_unit"] * r["count"]
            vendor_rows.append(r)
            continue
        # WoW-style stack-size x #stacks. Default to the largest legal stack and
        # as many whole stacks as the held quantity allows.
        r["max_stack"] = max(1, min(max_stacks.get(r["entry"], 1), r["count"]))
        r["def_stack"] = r["max_stack"]
        r["def_num"] = max(1, r["count"] // r["def_stack"])
        # Prefilled prices are per single item; the form edits per-stack totals.
        r["bid_gsc"] = _to_gsc(r["unit_price"] * r["def_stack"])
        r["buyout_gsc"] = _to_gsc(r["unit_price"] * r["def_stack"])
        auction_rows.append(r)

    return render_template(
        "auction_review.html",
        character=ch,
        rows=auction_rows,
        vendor_rows=vendor_rows,
        durations=AUCTION_DURATIONS,
        deposit_percent=current_app.config["AH_DEPOSIT_PERCENT"],
        deposit_rate=current_app.config["AH_DEPOSIT_RATE"],
        char_online=bool(ch.get("online")),
        money=int(ch.get("money") or 0),
    )


@bp.route("/list", methods=["POST"])
@login_required
def list_items():
    ch = active_character()
    if not ch:
        return redirect(url_for("auction.index"))
    if not soap.enabled():
        flash("Selling from the web isn't available right now.", "error")
        return redirect(url_for("auction.index"))

    # The page submits one JSON object that keeps the two actions separate:
    #   {"hours": 12,
    #    "auctions": [{"guids","stack_size","num_stacks","bid","buyout"}, ...],
    #    "vendor":   ["guid,guid", ...]}
    # A duration is only required when there are auctions; a vendor-only
    # submission needs no duration at all.
    try:
        payload = json.loads(request.form.get("payload") or "{}")
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError):
        flash("Couldn't read the submission. Please try again.", "error")
        return redirect(url_for("auction.index"))

    auctions = payload.get("auctions") or []
    vendor = payload.get("vendor") or []
    try:
        hours = int(payload.get("hours") or 0)
    except (ValueError, TypeError):
        hours = 0
    if auctions and hours not in AUCTION_DURATIONS:
        flash("Pick a valid auction duration.", "error")
        return redirect(url_for("auction.index"))

    by_guid = _sellable_by_guid(ch["guid"])
    instances = db.held_item_instances(ch["guid"])

    def _live_guids(csv):
        """Guids from a CSV that are still held & sellable, with their total
        available count."""
        guids, available = [], 0
        for part in str(csv).split(","):
            if not part.isdigit():
                continue
            g = int(part)
            if g not in by_guid:  # no longer held / sellable
                continue
            guids.append(g)
            available += int((instances.get(g) or {}).get("count", 1))
        return guids, available

    # Each auction posts one item as WoW does: stack size x number of stacks,
    # with the bid/buyout per *stack* (already copper in the payload).
    specs: list[str] = []
    for a in auctions if isinstance(auctions, list) else []:
        if not isinstance(a, dict):
            continue
        try:
            stack_size = int(a.get("stack_size"))
            num_stacks = int(a.get("num_stacks"))
            bid_stack = max(0, int(a.get("bid") or 0))
            buyout_stack = max(0, int(a.get("buyout") or 0))
        except (ValueError, TypeError):
            continue
        if stack_size < 1 or num_stacks < 1 or bid_stack <= 0:
            continue  # need a positive size, count and starting bid
        guids, available = _live_guids(a.get("guids", ""))
        if not guids or stack_size * num_stacks > available:
            continue
        specs.append(f"{stack_size}:{num_stacks}:{bid_stack}:{buyout_stack}:"
                     + ",".join(str(g) for g in guids))

    # Vendor (instant-sell) rows: the server re-validates each CSV and prices
    # the items at their vendor SellPrice.
    vendor_specs: list[str] = []
    for csv in vendor if isinstance(vendor, list) else []:
        guids, _ = _live_guids(csv)
        if guids:
            vendor_specs.append(",".join(str(g) for g in guids))

    if not specs and not vendor_specs:
        flash("Nothing valid to list -- prices or items may have changed.", "error")
        return redirect(url_for("auction.index"))

    if specs:
        ok, out = soap.command(
            f"guildhall list {ch['guid']} {hours} " + " ".join(specs))
        _flash_listing_result(ok, out)
    if vendor_specs:
        ok, out = soap.command(
            f"guildhall vendor {ch['guid']} " + " ".join(vendor_specs))
        _flash_vendor_result(ok, out)
    return redirect(url_for("auction.index"))
