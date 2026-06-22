#!/usr/bin/env python3
"""Build a static recipe reference (recipes.json) from the client DBC files.

Recipe names / profession mappings are NOT in MySQL on this server (the
*_dbc tables are empty), so we parse the on-disk DBCs the worldserver ships:

  SkillLineAbility.dbc  -> spell_id -> skill_line   (which profession a spell belongs to)
  Spell.dbc             -> spell_id -> name, created item id

Field offsets are taken from AzerothCore's own layout:
  * SkillLineAbilityfmt = "niiiixxiiiiixx"  -> SkillLine @field 1, Spell @field 2,
    MinSkillLineRank @7, TrivialSkillLineRankHigh @10.
  * Spell.dbc has 234 fields (936-byte records); offsets below match the
    acore_world.spell_dbc column order: ID @0, EffectItemType_1 @107,
    Name_Lang_enUS @136 (offset into the string block).

Run once (re-run only if the DBCs change):
    python build_recipes.py [--dbc-dir PATH] [--out recipes.json]
Default --dbc-dir is read from the worldserver DataDir if discoverable, else
../env/dist/data/dbc.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from professions import PROFESSION_SKILL_IDS

PROF_SET = set(PROFESSION_SKILL_IDS)

# Spell.dbc field byte-offsets (field_index * 4).
SPELL_ID_OFF = 0
SPELL_ITEM1_OFF = 107 * 4         # EffectItemType_1
SPELL_NAME_OFF = 136 * 4          # Name_Lang_enUS (string-block offset)
SPELL_REAGENT_OFF = 52 * 4        # Reagent_1..8 (item ids)
SPELL_REAGENT_COUNT_OFF = 60 * 4  # ReagentCount_1..8
SPELL_REAGENT_N = 8
SPELL_ICONID_OFF = 133 * 4        # SpellIconID

# ItemDisplayInfo.dbc: ID@0, InventoryIcon_1 (string) @field 5.
ITEMDISPLAY_ICON_OFF = 5 * 4
# SpellIcon.dbc: ID@0, TextureFilename (string) @field 1.
SPELLICON_TEXTURE_OFF = 1 * 4

# SkillLineAbility.dbc field byte-offsets.
SLA_SKILLLINE_OFF = 1 * 4
SLA_SPELL_OFF = 2 * 4
SLA_MINRANK_OFF = 7 * 4
SLA_TRIVIAL_HIGH_OFF = 10 * 4     # gray level
SLA_TRIVIAL_LOW_OFF = 11 * 4      # yellow level


def read_dbc(path: Path):
    """Return (records: list[bytes], string_block: bytes, field_count, rec_size)."""
    data = path.read_bytes()
    magic, rc, fc, rs, sbs = struct.unpack_from("<4sIIII", data, 0)
    if magic != b"WDBC":
        raise ValueError(f"{path}: not a WDBC file (magic={magic!r})")
    body = 20
    records = [data[body + i * rs: body + (i + 1) * rs] for i in range(rc)]
    string_block = data[body + rc * rs: body + rc * rs + sbs]
    return records, string_block, fc, rs


def cstr(block: bytes, offset: int) -> str:
    end = block.find(b"\x00", offset)
    raw = block[offset:end] if end >= 0 else block[offset:]
    # DBC strings are UTF-8 in 3.3.5a; fall back to latin-1 just in case.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def u32(rec: bytes, off: int) -> int:
    return struct.unpack_from("<I", rec, off)[0]


def parse_skill_line_ability(path: Path):
    """spell_id -> { skill_line: {"yellow":low, "green":mid, "gray":high} }.

    Difficulty thresholds (verified against in-game values): yellow = trivial-low,
    gray = trivial-high, green = midpoint. Drops bare profession rank-unlock spells.
    """
    records, _, _, _ = read_dbc(path)
    mapping: dict[int, dict[int, dict]] = {}
    for rec in records:
        skill_line = u32(rec, SLA_SKILLLINE_OFF)
        if skill_line not in PROF_SET:
            continue
        min_rank = u32(rec, SLA_MINRANK_OFF)
        low = u32(rec, SLA_TRIVIAL_LOW_OFF)
        high = u32(rec, SLA_TRIVIAL_HIGH_OFF)
        if min_rank == 0 and high == 0 and low == 0:
            continue  # profession rank-unlock spell, not a recipe
        spell = u32(rec, SLA_SPELL_OFF)
        green = (low + high) // 2 if high else 0
        mapping.setdefault(spell, {})[skill_line] = {
            "yellow": low, "green": green, "gray": high,
        }
    return mapping


def parse_spell_details(path: Path, wanted: set[int]):
    """spell_id -> {"name", "item", "reagents":[(item_id,count),...], "icon_id"}."""
    records, block, _, _ = read_dbc(path)
    out: dict[int, dict] = {}
    for rec in records:
        sid = u32(rec, SPELL_ID_OFF)
        if sid not in wanted:
            continue
        reagents = []
        for i in range(SPELL_REAGENT_N):
            item = u32(rec, SPELL_REAGENT_OFF + i * 4)
            count = u32(rec, SPELL_REAGENT_COUNT_OFF + i * 4)
            if 0 < item < 0x80000000 and count > 0:
                reagents.append((item, count))
        out[sid] = {
            "name": cstr(block, u32(rec, SPELL_NAME_OFF)),
            "item": u32(rec, SPELL_ITEM1_OFF),
            "reagents": reagents,
            "icon_id": u32(rec, SPELL_ICONID_OFF),
        }
    return out


def _icon_basename(path_str: str) -> str:
    """'Interface\\Icons\\Spell_Holy_X' or 'INV_Misc_Y' -> 'spell_holy_x' (lowercase
    basename, no extension), matching the Wowhead icon CDN naming."""
    name = path_str.replace("/", "\\").split("\\")[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.lower()


def parse_item_display_icons(path: Path) -> dict[int, str]:
    """ItemDisplayInfo ID -> inventory icon basename."""
    records, block, _, _ = read_dbc(path)
    out: dict[int, str] = {}
    for rec in records:
        out[u32(rec, SPELL_ID_OFF)] = _icon_basename(
            cstr(block, u32(rec, ITEMDISPLAY_ICON_OFF))
        )
    return out


def parse_spell_icons(path: Path) -> dict[int, str]:
    """SpellIcon ID -> icon basename."""
    records, block, _, _ = read_dbc(path)
    out: dict[int, str] = {}
    for rec in records:
        out[u32(rec, SPELL_ID_OFF)] = _icon_basename(
            cstr(block, u32(rec, SPELLICON_TEXTURE_OFF))
        )
    return out


def resolve_items(item_ids: set[int], db_args: dict) -> dict[int, tuple[str, int]]:
    """Look up item_template (name, displayid) so reagent/product names and icons
    can be baked in. Returns {} (graceful) if the DB is unreachable."""
    item_ids = {i for i in item_ids if i}
    if not item_ids:
        return {}
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=db_args["host"], port=db_args["port"],
            user=db_args["user"], password=db_args["password"],
            database=db_args["database"],
        )
    except Exception as exc:  # noqa: BLE001 - build script, report and continue
        print(f"  (item data unavailable: {exc})")
        return {}
    cur = conn.cursor()
    items: dict[int, tuple[str, int]] = {}
    ids = list(item_ids)
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT entry, name, displayid FROM item_template WHERE entry IN ({ph})",
            chunk,
        )
        for entry, name, displayid in cur.fetchall():
            items[entry] = (name, displayid)
    cur.close()
    conn.close()
    return items


def fetch_vendor_items(db_args: dict) -> list[int]:
    """Item entries freely buyable from a vendor (npc_vendor with maxcount = 0,
    i.e. unlimited stock) -- excluded from the in-demand list. Limited-stock
    sellers (maxcount > 0) are NOT counted, since gatherables like cloth/ore/herbs
    often have a vendor that stocks a handful; those should still be in demand.
    Empty list if DB unreachable."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=db_args["host"], port=db_args["port"],
            user=db_args["user"], password=db_args["password"],
            database=db_args["database"],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  (vendor items unavailable: {exc})")
        return []
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT item FROM npc_vendor WHERE item > 0 AND maxcount = 0")
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return ids


def discover_dbc_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "env/dist/data/dbc", here.parent / "data/dbc"):
        if (cand / "SkillLineAbility.dbc").exists():
            return cand
    return here.parent / "env/dist/data/dbc"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbc-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("recipes.json"))
    # item names (reagents/products) are baked in from item_template at build time,
    # so the running app needs no acore_world access.
    ap.add_argument("--db-host", default="127.0.0.1")
    ap.add_argument("--db-port", type=int, default=3306)
    ap.add_argument("--db-user", default="acore")
    ap.add_argument("--db-pass", default="acore")
    ap.add_argument("--db-name", default="acore_world")
    args = ap.parse_args()

    dbc_dir = args.dbc_dir or discover_dbc_dir()
    print(f"Reading DBCs from {dbc_dir}")
    spell_to_skills = parse_skill_line_ability(dbc_dir / "SkillLineAbility.dbc")
    print(f"  {len(spell_to_skills)} profession recipe spells")
    details = parse_spell_details(dbc_dir / "Spell.dbc", set(spell_to_skills))
    print(f"  resolved {len(details)} spell details")
    display_icons = parse_item_display_icons(dbc_dir / "ItemDisplayInfo.dbc")
    spell_icons = parse_spell_icons(dbc_dir / "SpellIcon.dbc")
    print(f"  {len(display_icons)} item icons, {len(spell_icons)} spell icons")

    # Collect every item id we want name/icon for (products + reagents).
    item_ids: set[int] = set()
    for d in details.values():
        if d["item"]:
            item_ids.add(d["item"])
        for rid, _ in d["reagents"]:
            item_ids.add(rid)
    print(f"Resolving {len(item_ids)} items from {args.db_name}")
    items = resolve_items(item_ids, {
        "host": args.db_host, "port": args.db_port, "user": args.db_user,
        "password": args.db_pass, "database": args.db_name,
    })
    print(f"  got {len(items)} item rows")

    def item_name(iid: int) -> str:
        return items[iid][0] if iid in items else f"item {iid}"

    def item_icon(iid: int) -> str:
        if iid in items:
            return display_icons.get(items[iid][1], "")
        return ""

    # recipes.json: { "<spell_id>": {name, item, icon, skills:{...},
    #                                 reagents:[{id,name,count,icon}]} }
    out: dict[str, dict] = {}
    for spell, skills in spell_to_skills.items():
        d = details.get(spell)
        if not d or not d["name"]:
            continue  # unnamed -> nothing useful to show
        # A real recipe either produces an item or consumes reagents. This drops
        # profession rank/specialization spells (e.g. "Blacksmithing", "Weaponsmith")
        # that are in the skill line but craft nothing.
        if not d["item"] and not d["reagents"]:
            continue
        reagents = [
            {"id": rid, "name": item_name(rid), "count": cnt, "icon": item_icon(rid)}
            for rid, cnt in d["reagents"]
        ]
        # Recipe icon: the crafted item's icon, else the spell's own icon (enchants).
        recipe_icon = item_icon(d["item"]) if d["item"] else ""
        if not recipe_icon:
            recipe_icon = spell_icons.get(d["icon_id"], "")
        out[str(spell)] = {
            "name": d["name"],
            "item": d["item"],
            "icon": recipe_icon,
            "skills": {str(s): t for s, t in skills.items()},
            "reagents": reagents,
        }
    args.out.write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    print(f"Wrote {len(out)} recipes to {args.out}")

    # Vendor items (excluded from the in-demand list).
    db_args = {
        "host": args.db_host, "port": args.db_port, "user": args.db_user,
        "password": args.db_pass, "database": args.db_name,
    }
    vendor = fetch_vendor_items(db_args)
    vendor_path = args.out.with_name("vendor_items.json")
    vendor_path.write_text(json.dumps(sorted(vendor), separators=(",", ":")))
    print(f"Wrote {len(vendor)} vendor item ids to {vendor_path}")


if __name__ == "__main__":
    main()
