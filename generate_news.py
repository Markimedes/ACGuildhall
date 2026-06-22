"""Pre-generate today's newspaper, decoupled from page views.

The News tab can generate lazily on first view, but that makes the unlucky first
visitor wait while the articles are written. Run this once a day -- just after the
AHPricingService rolls the new daily market event at midnight -- so the edition is
already cached and every visitor just reads it.

It reuses the same cache-first logic the web app uses (``app.todays_news``), so it
only generates the categories not already cached for today's event date; running it
twice in a day is a no-op.

Usage (configured from the same GUILDHALL_* env vars as the app):
    python generate_news.py

Wire it to run daily a few minutes after midnight (matching the AHPricingService's
timezone), e.g. a cron entry:
    5 0 * * *  cd /path/to/guildhall && python generate_news.py >> news.log 2>&1
or a small sidecar in docker-compose -- see DEPLOY.md.
"""

from __future__ import annotations

import logging
import sys

import ahservice
import db
import exploits
from app import NEWS_MARKET_CATEGORIES, todays_news
from config import ProductionConfig
from news_ai import NewsDesk


log = logging.getLogger("guildhall.news.cron")


def setup() -> NewsDesk:
    """Build config from the environment, open the DB pool once, configure the
    service modules this process uses, and build the desk. Call this once; a
    long-lived scheduler reuses the returned desk across daily runs."""
    cfg = ProductionConfig().validate()
    db.init_pool(cfg.DATABASE)
    ahservice.configure(cfg.AHPRICING)
    exploits.configure(cfg.EXPLOITS)
    return NewsDesk.from_env()


def run(desk: NewsDesk) -> bool:
    """Generate (and cache) today's missing articles -- market stories and Heroic
    Exploits. Cache-first, so calling it repeatedly only fills in what's missing.
    Returns True if the edition is INCOMPLETE (some story failed to generate this
    pass) and a retry is warranted; False when there is nothing left to do."""
    if not desk.available():
        log.warning("news desk offline (no key / SDK / model access); nothing to do")
        return False

    # Market stories -- only when an AH event is active today. Incomplete if a
    # category failed to generate (fewer than the expected sections are cached).
    events = ahservice.events()
    has_event = bool(events and events.get("enabled"))
    market = todays_news(desk, events) if has_event else []
    if not has_event:
        log.info("no active market event today")
    market_incomplete = has_event and len(market) < len(NEWS_MARKET_CATEGORIES)

    # Heroic Exploits + Obituaries -- player activity from the chronicle,
    # independent of the AH event.
    exploit_articles, exploits_incomplete = exploits.generate_today(
        desk, exploits.EXPLOITS_MAX, exploits.OBITUARIES_MAX)

    retry = market_incomplete or exploits_incomplete
    log.info("edition: %d market + %d exploit/obituary story/ies%s",
             len(market), len(exploit_articles),
             " (incomplete -- will retry)" if retry else "")
    return retry


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Exit 1 if the edition came out incomplete (a wrapper/cron may retry).
    return 1 if run(setup()) else 0


if __name__ == "__main__":
    sys.exit(main())
