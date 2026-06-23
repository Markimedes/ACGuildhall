"""Achievement id -> name/points, loaded from achievements.json.

The data is static (it comes from the client Achievement.dbc and never changes),
built once by build_achievements.py. ``acore_world.achievement_dbc`` is an empty
stub on a normal install, so the Heroic Exploits news desk resolves achievement
names from here instead (same approach as recipes.json).
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent

try:
    _ACH: dict[int, dict] = {
        int(k): v for k, v in
        json.loads((_HERE / "achievements.json").read_text(encoding="utf-8")).items()
    }
except FileNotFoundError:
    _ACH = {}


def name(achievement_id: int) -> str:
    """Achievement title, or "" if unknown (e.g. JSON not built / stale id)."""
    a = _ACH.get(achievement_id)
    return a["name"] if a else ""


def points(achievement_id: int) -> int:
    a = _ACH.get(achievement_id)
    return a["points"] if a else 0
