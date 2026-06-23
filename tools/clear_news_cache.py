#!/usr/bin/env python3
"""Clear today's cached news so the desk regenerates it on the next pass.

The News tab is cache-first: market stories live in ``guildhall.news`` (keyed by
the AH event date) and Heroic Exploits / Obituaries in ``guildhall.exploit_news``
(keyed by edition date). Once cached, an edition is never rewritten. This tool
deletes those rows for today so the next ``generate_news.py`` run (or the next
page view) rebuilds them -- handy after changing prompts.

Standalone: it adds the Guildhall package to the import path itself, loads the
project's .env if present, and builds its own DB config from the GUILDHALL_DB_*
environment -- it does NOT import the Flask app.

Usage (from anywhere):
    python tools/clear_news_cache.py                 # clear ALL of today, with prompt
    python tools/clear_news_cache.py --dry-run       # show what would be cleared
    python tools/clear_news_cache.py --kind obituaries
    python tools/clear_news_cache.py --date 2026-06-17 --yes
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

# --- standalone bootstrap: find the Guildhall package + load its .env --------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TOOLS_DIR)  # repo root (holds the data/ package)
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
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _db_config() -> dict:
    """The [database] section, straight from GUILDHALL_DB_* (same vars the app
    uses) -- without importing app.py and pulling in Flask."""
    env = os.environ
    cfg: dict = {}
    if "GUILDHALL_DB_HOST" in env:
        cfg["host"] = env["GUILDHALL_DB_HOST"]
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
from data import db, exploits  # noqa: E402


def _counts(edition: str) -> dict[str, int]:
    """How many cached rows exist today, split market / exploits / obituaries."""
    market = len(db.news_get(edition))
    rows = db.exploit_news_get(edition)
    obit = sum(1 for r in rows if exploits.is_obituary(r["story_key"]))
    return {"market": market, "obituaries": obit, "exploits": len(rows) - obit}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=("all", "market", "exploits", "obituaries"),
                    default="all", help="which cache to clear (default: all)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="edition date YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be cleared, delete nothing")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    ap.add_argument("--env", default=os.path.join(_PKG_DIR, ".env"),
                    help="path to a .env to load (default: the package .env)")
    args = ap.parse_args()

    _load_env_file(args.env)
    db.init_pool(_db_config())

    edition = args.date
    have = _counts(edition)
    want = ("market", "exploits", "obituaries") if args.kind == "all" \
        else (args.kind,)
    total = sum(have[k] for k in want)

    print(f"Cached for {edition}: "
          + ", ".join(f"{have[k]} {k}" for k in ("market", "exploits",
                                                 "obituaries")))
    print(f"To clear ({args.kind}): {total} row(s).")

    if total == 0:
        print("Nothing to clear.")
        return 0
    if args.dry_run:
        print("(dry run -- nothing deleted)")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("refusing to delete without a TTY; pass --yes to confirm.")
        if input(f"Delete {total} cached row(s) for {edition}? [y/N] "
                 ).strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    removed = 0
    if args.kind in ("all", "market"):
        removed += db.news_clear(edition)
    if args.kind in ("all", "exploits"):
        removed += db.exploit_news_clear(edition, "exploits")
    if args.kind in ("all", "obituaries"):
        removed += db.exploit_news_clear(edition, "obituaries")

    print(f"Cleared {removed} row(s) for {edition}. They will regenerate on the "
          "next generate_news.py run or page view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
