"""Character race/class id -> display name.

Race and class ids live in ChrRaces.dbc / ChrClasses.dbc, not in MySQL, so the
panel carries its own static map (like professions.py). These are the standard
WotLK (3.3.5a) ids as stored in acore_characters.characters.race / .class.
"""

RACES = {
    1: "Human",
    2: "Orc",
    3: "Dwarf",
    4: "Night Elf",
    5: "Undead",
    6: "Tauren",
    7: "Gnome",
    8: "Troll",
    10: "Blood Elf",
    11: "Draenei",
}

CLASSES = {
    1: "Warrior",
    2: "Paladin",
    3: "Hunter",
    4: "Rogue",
    5: "Priest",
    6: "Death Knight",
    7: "Shaman",
    8: "Mage",
    9: "Warlock",
    11: "Druid",
}


def race_name(race_id: int) -> str:
    return RACES.get(race_id, "")


def class_name(class_id: int) -> str:
    return CLASSES.get(class_id, "")
