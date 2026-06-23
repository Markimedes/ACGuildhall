"""Skill-line id -> profession name.

Skill names live in SkillLine.dbc, not in MySQL, so the panel carries its own
static map. These are the standard WotLK (3.3.5a) profession/secondary skill ids.
"""

# Primary professions.
PRIMARY_PROFESSIONS = {
    164: "Blacksmithing",
    165: "Leatherworking",
    171: "Alchemy",
    182: "Herbalism",
    186: "Mining",
    197: "Tailoring",
    202: "Engineering",
    333: "Enchanting",
    393: "Skinning",
    755: "Jewelcrafting",
    773: "Inscription",
}

# Secondary skills.
SECONDARY_SKILLS = {
    129: "First Aid",
    185: "Cooking",
    356: "Fishing",
}

PROFESSIONS = {**PRIMARY_PROFESSIONS, **SECONDARY_SKILLS}

# Convenient list for SQL IN (...) clauses.
PROFESSION_SKILL_IDS = tuple(sorted(PROFESSIONS))

# Categories for display: crafting (make things), gathering (collect mats),
# general (secondary skills).
CRAFTING_SKILLS = {164, 165, 171, 197, 202, 333, 755, 773}
GATHERING_SKILLS = {182, 186, 393}
GENERAL_SKILLS = {129, 185, 356}

# Display order of categories.
CATEGORY_ORDER = ("Crafting", "Gathering", "General")
_CATEGORY_OF = {
    **{s: "Crafting" for s in CRAFTING_SKILLS},
    **{s: "Gathering" for s in GATHERING_SKILLS},
    **{s: "General" for s in GENERAL_SKILLS},
}


def profession_name(skill_id: int) -> str:
    return PROFESSIONS.get(skill_id, f"Skill {skill_id}")


def category_of(skill_id: int) -> str:
    return _CATEGORY_OF.get(skill_id, "General")
