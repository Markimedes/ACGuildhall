"""Roster blueprint: the read-only guild profession roster, per-member in-demand
reagents, the surplus matcher, and individual player profiles. Mounted at
/roster.
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, abort, current_app, render_template
from flask_login import login_required

from data import db, professions, recipes
from data.professions import profession_name
from guildhall.core import current_guild

bp = Blueprint("roster", __name__)


def _demand_for(guid):
    """Per-profession demand for a character (cache-first; computes on miss).
    Returns {skill_id: [items]}. Used to aggregate guild-wide on the roster."""
    row = db.demand_get(guid)
    ttl = current_app.config["DEMAND_REFRESH_MINUTES"]
    if row and row["age_minutes"] is not None and row["age_minutes"] < ttl:
        return {int(k): v for k, v in json.loads(row["items"]).items()}
    profs = db.character_professions(guid)
    skill_values = {p["skill"]: p["value"] for p in profs}
    known = db.character_known_spell_ids(guid)
    demand, _ = _player_demand(guid, skill_values, known)
    return demand


def _player_demand(guid, skill_values, known):
    """Per-profession in-demand base mats, cached and recomputed past the TTL.
    Returns ({skill_id: [items]}, computed_at)."""
    row = db.demand_get(guid)
    ttl = current_app.config["DEMAND_REFRESH_MINUTES"]
    if row and row["age_minutes"] is not None and row["age_minutes"] < ttl:
        data = json.loads(row["items"])
        return {int(k): v for k, v in data.items()}, row["computed_at"]
    # "need" = how many of that profession's skill-up recipes use the base
    # item (crafted intermediates decomposed, vendor items excluded).
    by_prof = recipes.reagent_demand_by_profession(known, skill_values)
    out: dict = {}
    for skill, items in by_prof.items():
        lst = [
            {"id": iid, "name": info["name"], "icon": info.get("icon", ""),
             "need": info["count"]}
            for iid, info in items.items()
        ]
        lst.sort(key=lambda d: (-d["need"], d["name"]))
        out[skill] = lst
    db.demand_store(guid, json.dumps({str(k): v for k, v in out.items()}))
    return out, datetime.now()


@bp.route("")
@login_required
def index():
    guild = current_guild()
    # skill_id -> list of members with that profession (skill desc).
    by_skill: dict = {}
    member_demand: dict = {}
    surplus: list = []
    if guild:
        member_guids = set()
        names: dict = {}
        for row in db.guild_professions(guild["guildid"]):
            by_skill.setdefault(row["skill"], []).append(row)
            member_guids.add(row["char_guid"])
            names[row["char_guid"]] = row["char_name"]
        for members in by_skill.values():
            members.sort(key=lambda m: (-m["value"], m["char_name"].lower()))
        # Per-member in-demand (for the per-character dropdown rows).
        member_demand = {guid: _demand_for(guid) for guid in member_guids}

        # Surplus: items the active char holds that others need but they don't.
        active = guild["char_guid"]
        demanded_by: dict = {}      # item_id -> {id,name,icon,needers:[...]}
        for guid, skills in member_demand.items():
            if guid == active:
                continue
            # which professions (skills) this member needs each item for
            per_item: dict = {}
            for skill, lst in skills.items():
                for it in lst:
                    e = per_item.setdefault(
                        it["id"], {"name": it["name"],
                                   "icon": it["icon"], "skills": set()})
                    e["skills"].add(skill)
            for iid, info in per_item.items():
                d = demanded_by.setdefault(
                    iid, {"id": iid, "name": info["name"],
                          "icon": info["icon"], "needers": []})
                d["needers"].append({
                    "name": names.get(guid, "?"), "guid": guid,
                    "professions": [profession_name(s)
                                    for s in sorted(info["skills"])],
                })
        own = member_demand.get(active, {})
        own_ids = {it["id"] for lst in own.values() for it in lst}
        held = db.character_held_items(active)
        surplus = [
            demanded_by[iid] for iid in held
            if iid in demanded_by and iid not in own_ids
        ]
        for s in surplus:
            s["needers"].sort(key=lambda n: n["name"].lower())
        surplus.sort(key=lambda s: (-len(s["needers"]), s["name"]))

    # Group professions into Crafting / Gathering / General categories.
    categories = []
    for cat in professions.CATEGORY_ORDER:
        profs = [
            {"skill": s, "name": profession_name(s), "members": by_skill[s]}
            for s in sorted(by_skill, key=profession_name)
            if professions.category_of(s) == cat
        ]
        if profs:
            categories.append({"name": cat, "professions": profs})
    return render_template(
        "roster.html", guild=guild, categories=categories,
        member_demand=member_demand, surplus=surplus,
    )


@bp.route("/player/<int:guid>")
@login_required
def player(guid):
    guild = current_guild()
    target = db.guild_member_char(guid)
    # Only viewable if the target shares the viewer's guild.
    if not guild or not target or target["guildid"] != guild["guildid"]:
        abort(404)
    profs = db.character_professions(guid)
    skill_values = {p["skill"]: p["value"] for p in profs}
    known = db.character_known_spell_ids(guid)
    recipe_groups = recipes.recipes_for_known(known, skill_values)
    demand_by_prof, demand_at = _player_demand(guid, skill_values, known)

    # Group the recipe sections by category for display.
    cats: dict = {}
    for g in recipe_groups:
        cats.setdefault(professions.category_of(g["skill"]), []).append(g)
    recipe_categories = [
        {"name": c, "groups": cats[c]}
        for c in professions.CATEGORY_ORDER if c in cats
    ]
    return render_template(
        "player.html", target=target, profs=profs,
        recipe_categories=recipe_categories, recipes_available=recipes.available(),
        demand_by_prof=demand_by_prof, demand_at=demand_at,
    )
