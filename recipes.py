"""Static recipe reference loaded from recipes.json (built by build_recipes.py).

Maps a character's known spell ids to recipes grouped by profession, with a
difficulty colour computed from the player's current skill and the recipe's
thresholds, plus the recipe's reagents.
"""

from __future__ import annotations

import json
from pathlib import Path

from professions import profession_name

_PATH = Path(__file__).with_name("recipes.json")
try:
    _RECIPES: dict[str, dict] = json.loads(_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:  # panel still runs; recipe lists just come back empty
    _RECIPES = {}

try:
    _VENDOR_ITEMS: set[int] = set(
        json.loads(Path(__file__).with_name("vendor_items.json").read_text())
    )
except FileNotFoundError:
    _VENDOR_ITEMS = set()

# item_id -> producers: [(skill_lines, reagents), ...]. Used to decompose a crafted
# intermediate (e.g. Bolt of Silk Cloth -> Silk Cloth) -- but only when the player
# actually has a profession that can craft it.
_CRAFTED: dict[int, list[tuple]] = {}
for _entry in _RECIPES.values():
    _item = _entry.get("item")
    if _item and _entry.get("reagents"):
        _skills = frozenset(int(s) for s in _entry["skills"])
        _CRAFTED.setdefault(_item, []).append((_skills, _entry["reagents"]))

# Hardest -> easiest. Lower rank sorts first.
_COLOR_RANK = {"orange": 0, "yellow": 1, "green": 2, "gray": 3}


def _expand_reagent(reagent: dict, seen: frozenset, player_skills: set) -> list[tuple]:
    """Yield a reagent AND, if it's a crafted intermediate the player can make,
    its sub-reagents recursively. Decomposition only happens when the player has a
    profession that crafts the intermediate -- a Jewelcrafter who needs Mana Potion
    but isn't an Alchemist needs the potion, not its herbs. Returns [(id,name,icon)]."""
    rid = reagent["id"]
    out = [(rid, reagent["name"], reagent.get("icon", ""))]
    if rid in _CRAFTED and rid not in seen:
        for skills, sub_reagents in _CRAFTED[rid]:
            if player_skills & skills:  # player can craft this intermediate
                for sub in sub_reagents:
                    out.extend(_expand_reagent(sub, seen | {rid}, player_skills))
                break
    return out


def available() -> bool:
    return bool(_RECIPES)


def _color(skill_value: int, thresholds: dict) -> str:
    """WoW trade-skill colour for a recipe given the player's current skill.
    yellow/green/gray come from the DBC trivial ranks; below yellow is orange."""
    gray = thresholds.get("gray", 0)
    green = thresholds.get("green", 0)
    yellow = thresholds.get("yellow", 0)
    if gray and skill_value >= gray:
        return "gray"
    if green and skill_value >= green:
        return "green"
    if yellow and skill_value >= yellow:
        return "yellow"
    return "orange"


def reagent_demand(
    known_spell_ids, skill_values: dict[int, int],
    colors=("orange", "yellow"),
) -> dict[int, dict]:
    """Reagents useful for the player's skill-up (orange/yellow) recipes.

    The count is **how many of those recipes use the reagent** (a demand-breadth
    score for "what to send"), not a quantity to craft. Crafted intermediates are
    listed AND expanded into their sub-reagents (so both Medium Leather and Light
    Leather appear); vendor-bought items are excluded.

    Returns {item_id: {"name", "icon", "count"}}.
    """
    player_skills = set(skill_values)
    req: dict[int, dict] = {}
    for sid in known_spell_ids:
        entry = _RECIPES.get(str(sid))
        if not entry:
            continue
        # Count a recipe once even if it appears in several skill lines.
        if not any(
            _color(skill_values.get(int(s), 0), t) in colors
            for s, t in entry["skills"].items()
        ):
            continue
        # Distinct items this recipe needs (intermediates + craftable components),
        # counted once per recipe.
        items: dict[int, tuple] = {}
        for ing in entry.get("reagents", []):
            for bid, bname, bicon in _expand_reagent(ing, frozenset(), player_skills):
                if bid in _VENDOR_ITEMS:
                    continue
                items[bid] = (bname, bicon)
        for bid, (bname, bicon) in items.items():
            r = req.setdefault(bid, {"name": bname, "icon": bicon, "count": 0})
            r["count"] += 1
    return req


def reagent_demand_by_profession(
    known_spell_ids, skill_values: dict[int, int],
    colors=("orange", "yellow"),
) -> dict[int, dict]:
    """Like reagent_demand but split per profession skill line, so each
    profession can show its own in-demand mats. Returns
    {skill_id: {item_id: {"name","icon","count"}}}; count = how many of that
    profession's skill-up recipes use the base item."""
    player_skills = set(skill_values)
    result: dict[int, dict] = {}
    for sid in known_spell_ids:
        entry = _RECIPES.get(str(sid))
        if not entry:
            continue
        for skill_str, thresholds in entry["skills"].items():
            skill = int(skill_str)
            if _color(skill_values.get(skill, 0), thresholds) not in colors:
                continue
            bases: dict[int, tuple] = {}
            for ing in entry.get("reagents", []):
                for bid, bname, bicon in _expand_reagent(ing, frozenset(), player_skills):
                    if bid in _VENDOR_ITEMS:
                        continue
                    bases[bid] = (bname, bicon)
            d = result.setdefault(skill, {})
            for bid, (bname, bicon) in bases.items():
                r = d.setdefault(bid, {"name": bname, "icon": bicon, "count": 0})
                r["count"] += 1
    return result


def recipes_for_known(known_spell_ids, skill_values: dict[int, int]) -> list[dict]:
    """Group known recipes by profession, ordered most-difficult first.

    ``skill_values`` maps a profession skill-line id to the player's current
    value, used to colour each recipe. Returns a list of
    {skill, profession, recipes:[{name, color, reagents}]}.
    """
    groups: dict[int, list[dict]] = {}
    for sid in known_spell_ids:
        entry = _RECIPES.get(str(sid))
        if not entry:
            continue
        for skill_str, thresholds in entry["skills"].items():
            skill = int(skill_str)
            color = _color(skill_values.get(skill, 0), thresholds)
            groups.setdefault(skill, []).append({
                "name": entry["name"],
                "icon": entry.get("icon", ""),
                "item": entry.get("item", 0),
                "spell": int(sid),
                "color": color,
                "reagents": entry.get("reagents", []),
            })
    out = []
    for skill, recs in groups.items():
        recs.sort(key=lambda r: (_COLOR_RANK[r["color"]], r["name"]))
        out.append(
            {"skill": skill, "profession": profession_name(skill), "recipes": recs}
        )
    out.sort(key=lambda g: g["profession"])
    return out
