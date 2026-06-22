#!/usr/bin/env python3
"""Print the RAW prompts the news desk would send, built from the day's events
-- without calling the LLM or touching the cache. A review tool: run it to eye
the exact instructions (length scale, persona, voice, tendency, quest detail,
witnesses, ...) that go to the model.

Standalone: it adds the Guildhall package to the import path itself, loads the
project's .env if present, and builds its own DB config from the GUILDHALL_DB_*
environment -- it does NOT import the Flask app.

Usage (from anywhere):
    python tools/sample_prompts.py                 # today's window
    python tools/sample_prompts.py --window 720    # widen the chronicle window
    python tools/sample_prompts.py --kind exploits # only one kind
    python tools/sample_prompts.py --no-market     # skip market stories
    python tools/sample_prompts.py --env /path/to/.env

Nothing is generated or written; this only assembles and prints prompt text.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

# --- standalone bootstrap: find the Guildhall package + load its .env --------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TOOLS_DIR)  # the guildhall/ package root
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


def _load_env_file(path: str) -> None:
    """Minimal KEY=VALUE .env loader (no dependency). Existing environment
    variables win, so an explicitly-set var is never overridden."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            os.environ.setdefault(key, value)


def _db_config() -> dict:
    """The [database] section, straight from GUILDHALL_DB_* (same vars the app
    uses) -- without importing app.py and pulling in Flask."""
    env = os.environ
    cfg: dict = {}
    if "GUILDHALL_DB_HOST" in env:
        cfg["host"] = env["GUILDHALL_DB_HOST"]
        cfg["host"] = "127.0.0.1"
    if "GUILDHALL_DB_PORT" in env:
        cfg["port"] = int(env["GUILDHALL_DB_PORT"])
    if "GUILDHALL_DB_USER" in env:
        cfg["user"] = env["GUILDHALL_DB_USER"]
    if "GUILDHALL_DB_PASSWORD" in env:
        cfg["password"] = env["GUILDHALL_DB_PASSWORD"]
    if "GUILDHALL_DB_POOL_SIZE" in env:
        cfg["pool_size"] = int(env["GUILDHALL_DB_POOL_SIZE"])
    missing = [f"GUILDHALL_DB_{k.upper()}" for k in ("host", "user", "password")
               if not cfg.get(k)]
    if missing:
        sys.exit("missing required env: " + ", ".join(missing)
                 + " (set them or pass --env path/to/.env)")
    return cfg


# Imports that need the package on sys.path happen after the bootstrap above.
import ahservice  # noqa: E402
import db  # noqa: E402
import exploits  # noqa: E402
import news_prompts  # noqa: E402

# Market story categories (mirrors app.NEWS_MARKET_CATEGORIES without the import).
MARKET_CATEGORIES = (
    news_prompts.PROFESSIONAL_DIGEST,
    news_prompts.GEAR_FOR_YOU,
    news_prompts.PRIMARY_STATS,
)


def _banner(title: str) -> None:
    print("\n" + "#" * 80)
    print(f"# {title}")
    print("#" * 80)


def _market_prompts() -> None:
    events = ahservice.events()
    if not events or not events.get("enabled"):
        print("\n(no active market event today -- no market stories)")
        return
    for cat in MARKET_CATEGORIES:
        reporter = news_prompts.pick_reporter(cat, seed=events.get("date"))
        _banner(f"MARKET / {news_prompts.CATEGORY_TITLES[cat]} "
                f"/ by {reporter.byline} [{reporter.key}]")
        print(news_prompts.market_prompt(cat, reporter, events))


def _exploit_prompts(ex: list) -> None:
    for spec in ex:
        reporter = news_prompts.pick_reporter(news_prompts.HEROIC_EXPLOITS,
                                              seed=spec["story_key"])
        if spec["kind"] == "group":
            scale = spec["group"].get("scale", "standard")
            _banner(f"GROUP EXPLOIT / {spec['subject']} / scale={scale} "
                    f"/ by {reporter.byline} [{reporter.key}]")
            print(news_prompts.group_exploits_prompt(reporter, spec["group"]))
        else:
            scale = spec["character"].get("scale", "standard")
            _banner(f"EXPLOIT {spec['story_key']} / {spec['subject']} "
                    f"/ scale={scale} / by {reporter.byline} [{reporter.key}]")
            print(news_prompts.exploits_prompt(reporter, spec["character"]))


def _obituary_prompts(ob: list) -> None:
    for spec in ob:
        reporter = news_prompts.pick_reporter(news_prompts.OBITUARIES,
                                              seed=spec["story_key"])
        lvl = spec["character"].get("death", {}).get("level")
        _banner(f"OBITUARY {spec['story_key']} / {spec['subject']} "
                f"/ level={lvl} / by {reporter.byline} [{reporter.key}]")
        print(news_prompts.obituary_prompt(reporter, spec["character"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="edition date YYYY-MM-DD (default: today). An edition "
                         "reports the calendar day BEFORE it, so use tomorrow's "
                         "date to preview today's deeds.")
    ap.add_argument("--kind", choices=("all", "market", "exploits", "obituaries"),
                    default="all", help="which prompts to print")
    ap.add_argument("--no-market", action="store_true",
                    help="skip market stories even when --kind=all")
    ap.add_argument("--env", default=os.path.join(_PKG_DIR, ".env"),
                    help="path to a .env to load (default: the package .env)")
    args = ap.parse_args()

    _load_env_file(args.env)
    db.init_pool(_db_config())
    # The service modules no longer read env at import; hand them their config
    # from the same env we just loaded (config.py has no Flask dependency).
    from config import Config
    _cfg = Config()
    ahservice.configure(_cfg.AHPRICING)
    exploits.configure(_cfg.EXPLOITS)

    covered = (datetime.date.fromisoformat(args.date)
               - datetime.timedelta(days=1)).isoformat()
    print(f"# edition {args.date} -- reporting deeds from {covered}")

    if args.kind in ("all", "market") and not args.no_market:
        _market_prompts()

    # Selection drives both exploits and obituaries, so do it once up front.
    ex, ob = [], []
    if args.kind in ("all", "exploits", "obituaries"):
        ex, ob = exploits.select_stories(args.date, exploits.EXPLOITS_MAX,
                                         exploits.OBITUARIES_MAX)

    if args.kind in ("all", "exploits"):
        _exploit_prompts(ex)
    if args.kind in ("all", "obituaries"):
        _obituary_prompts(ob)

    print(f"\n# done: {len(ex)} exploit + {len(ob)} obituary spec(s) "
          f"for edition {args.date} (deeds from {covered}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
