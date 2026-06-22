"""Build a static achievement reference (achievements.json) from Achievement.dbc.

Achievement names/points are NOT reliably in MySQL -- ``acore_world.achievement_dbc``
is a near-empty stub on a normal AzerothCore install (same story as the other
``*_dbc`` tables; see build_recipes.py). The real data lives in the client
``Achievement.dbc`` the worldserver ships, so we parse it once into a small JSON the
Heroic Exploits news desk loads at runtime (mirrors recipes.json / item_icons.json).

Achievement.dbc (3.3.5a, build 12340): 62 fields, 248-byte records.
  ID                 @ field 0
  Title_Lang_enUS    @ field 4   (string-block offset)
  Points             @ field 39

Run once (re-run only if the DBCs change):
    python build_achievements.py [--dbc-dir PATH] [--out achievements.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_recipes import cstr, discover_dbc_dir, read_dbc, u32

ACH_ID_OFF = 0 * 4
ACH_NAME_OFF = 4 * 4
ACH_POINTS_OFF = 39 * 4


def parse_achievements(path: Path) -> dict[int, dict]:
    """id -> {"name": str, "points": int} for every achievement with a name."""
    records, strings, _, _ = read_dbc(path)
    out: dict[int, dict] = {}
    for rec in records:
        name = cstr(strings, u32(rec, ACH_NAME_OFF)).strip()
        if not name:
            continue
        out[u32(rec, ACH_ID_OFF)] = {
            "name": name,
            "points": u32(rec, ACH_POINTS_OFF),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbc-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).with_name("achievements.json"))
    args = ap.parse_args()

    dbc_dir = args.dbc_dir or discover_dbc_dir()
    print(f"Reading Achievement.dbc from {dbc_dir}")
    achievements = parse_achievements(dbc_dir / "Achievement.dbc")
    print(f"  {len(achievements)} achievements")
    # Compact, sorted by id for a stable diff.
    args.out.write_text(
        json.dumps({str(k): achievements[k] for k in sorted(achievements)},
                   separators=(",", ":")),
        encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
