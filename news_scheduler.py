"""Daily scheduler daemon for the news desk -- the docker-compose sidecar.

Generates today's edition once at startup (so a freshly-started stack has a paper),
then once a day shortly after midnight, just after the AHPricingService rolls the
new daily market event. This runs as ONE process (unlike the 3 gunicorn web
workers), so generation happens exactly once per day with no worker races.

The web app keeps lazy first-view generation as a fallback, so a missed run (e.g.
the pricing service was briefly down at startup) still self-heals on the next view.

Config (container local time -- set TZ to match the AHPricingService so "midnight"
agrees on both sides):
  GUILDHALL_NEWS_HOUR    hour of the daily run   (default 0)
  GUILDHALL_NEWS_MINUTE  minute of the daily run (default 5)
"""

from __future__ import annotations

import datetime
import logging
import os
import time

import generate_news

HOUR = int(os.environ.get("GUILDHALL_NEWS_HOUR", "0"))
MINUTE = int(os.environ.get("GUILDHALL_NEWS_MINUTE", "5"))
# When a generation pass comes out incomplete (e.g. a transient model 503/504),
# retry this often until the edition is complete, instead of waiting a full day --
# but give up after MAX_RETRIES attempts and wait for the next daily run.
RETRY_SECONDS = int(os.environ.get("GUILDHALL_NEWS_RETRY_SECONDS", "300"))
MAX_RETRIES = int(os.environ.get("GUILDHALL_NEWS_MAX_RETRIES", "20"))


def _seconds_until_next(hour: int, minute: int) -> float:
    """Seconds from now until the next HH:MM in local time (tomorrow if passed)."""
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def _pass(desk, log) -> bool:
    """Run one generation pass; return True if it was incomplete (retry wanted).
    Never raises -- a crash is treated as incomplete so the loop keeps trying."""
    try:
        return generate_news.run(desk)
    except Exception:  # noqa: BLE001 -- never let one bad run kill the daemon
        log.exception("generation pass failed")
        return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("guildhall.news.scheduler")

    desk = generate_news.setup()  # config + DB pool, once
    log.info("news scheduler up; daily run at %02d:%02d, retry every %ds up to %d "
             "times until complete (container local time)",
             HOUR, MINUTE, RETRY_SECONDS, MAX_RETRIES)

    # Warm today's edition immediately so startup doesn't wait for midnight.
    incomplete = _pass(desk, log)
    retries = 0

    while True:
        # If the last pass left something ungenerated (transient model error),
        # retry soon -- up to MAX_RETRIES; otherwise (complete, or budget spent)
        # reset and sleep until the next daily edition.
        if incomplete and retries < MAX_RETRIES:
            retries += 1
            delay = RETRY_SECONDS
            log.info("edition incomplete; retry %d/%d in %ds",
                     retries, MAX_RETRIES, RETRY_SECONDS)
        else:
            if incomplete:
                log.warning("edition still incomplete after %d retries; giving up "
                            "until the next daily run", MAX_RETRIES)
            retries = 0
            delay = _seconds_until_next(HOUR, MINUTE)
            log.info("next edition in %.1f h", delay / 3600)
        time.sleep(delay)
        incomplete = _pass(desk, log)


if __name__ == "__main__":
    main()
