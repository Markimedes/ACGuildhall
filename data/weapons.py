"""Weapon item subclass + weapon skill-line id -> display name.

Weapon types live in Item.dbc (item_template.class/subclass) and the weapon
proficiencies a character has trained live in acore_characters.character_skills
keyed by SkillLine.dbc ids. Neither name set is in MySQL as text, so -- like
chartypes.py and professions.py -- the panel carries its own static map. These
are the standard WotLK (3.3.5a) ids.

Used by the Heroic Exploits desk to tell which weapons a boss-slayer wields and
which they are most practiced with (correlating equipped weapons against their
weapon skill points).
"""

ITEM_CLASS_WEAPON = 2

# item_template.subclass when item_template.class = 2 (weapons).
WEAPON_SUBCLASS = {
    0: "one-handed axe",
    1: "two-handed axe",
    2: "bow",
    3: "gun",
    4: "one-handed mace",
    5: "two-handed mace",
    6: "polearm",
    7: "one-handed sword",
    8: "two-handed sword",
    10: "staff",
    13: "fist weapon",
    15: "dagger",
    16: "thrown weapon",
    18: "crossbow",
    19: "wand",
}

# acore_characters.character_skills.skill -> weapon proficiency name
# (SkillLine.dbc ids). Only the weapon skills; profession/class lines are
# resolved elsewhere (professions.py) or ignored.
WEAPON_SKILL = {
    43: "Swords",
    44: "Axes",
    45: "Bows",
    46: "Guns",
    54: "Maces",
    55: "Two-Handed Swords",
    95: "Daggers",
    136: "Staves",
    160: "Two-Handed Maces",
    162: "Unarmed",
    172: "Two-Handed Axes",
    176: "Thrown",
    226: "Crossbows",
    228: "Wands",
    229: "Polearms",
    473: "Fist Weapons",
}


def weapon_type_name(subclass: int) -> str:
    """Display name for a weapon's item subclass, or a generic fallback."""
    return WEAPON_SUBCLASS.get(subclass, "weapon")


def weapon_skill_name(skill_id: int) -> str:
    """Display name for a weapon proficiency skill line, or '' if not a weapon
    skill (so callers can filter profession/class lines out)."""
    return WEAPON_SKILL.get(skill_id, "")
