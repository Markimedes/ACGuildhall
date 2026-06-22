"""Client for the AHPricingService -- the realm's sole AH pricing authority.

The service prices any item from its entry id (override base, or the bot's
built-in formula computed from item_template, plus the rolled random
property/suffix uplift), answering ``200 "<buyout>,<bid>"`` (copper, per single
item). The Auction House tab gets every price from here -- there is no local
pricing fallback. On any failure (service down, timeout, 204, bad body) ``price``
returns None and the tab shows the item as "Not priced"; the page still renders.

Config:
  GUILDHALL_AHPRICING_URL      default http://ahpricingservice:8089/price
  GUILDHALL_AHPRICING_TIMEOUT  per-call timeout in seconds (default 1.5)
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

# Static defaults; overridden by configure() at startup so config is never read
# at import time. The /health and /events URLs are derived from the base.
URL = "http://ahpricingservice:8089/price"
TIMEOUT = 1.5
_BASE = URL[: -len("/price")] if URL.endswith("/price") else URL
_HEALTH_URL = _BASE + "/health" if URL.endswith("/price") else URL
_EVENTS_URL = _BASE + "/events"


def configure(cfg: dict) -> None:
    """Set the pricing-service URL/timeout from a config dict (see
    config.Config.AHPRICING) and re-derive the /health and /events URLs."""
    global URL, TIMEOUT, _BASE, _HEALTH_URL, _EVENTS_URL
    URL = cfg.get("url") or URL
    TIMEOUT = float(cfg.get("timeout") or TIMEOUT)
    _BASE = URL[: -len("/price")] if URL.endswith("/price") else URL
    _HEALTH_URL = _BASE + "/health" if URL.endswith("/price") else URL
    _EVENTS_URL = _BASE + "/events"


def available() -> bool:
    """Whether the pricing service is reachable (its /health returns 200)."""
    try:
        with urllib.request.urlopen(_HEALTH_URL, timeout=TIMEOUT) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def events() -> dict | None:
    """Today's daily market event (the "fun factor") from the service's /events,
    or None if there's no active event or the service is unreachable. The dict
    carries the buffed profession/category/stat and each buff's multiplier, plus
    the mirror-image slump picks (``discount_*``) trading at a discount today."""
    try:
        with urllib.request.urlopen(_EVENTS_URL, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 -- any failure -> no banner, page still renders
        return None
    if not isinstance(data, dict):
        return None
    # Show the panel/news when EITHER the buffs or the slump are active.
    if not (data.get("enabled") or data.get("discount_enabled")):
        return None
    return data


def price(entry: int, random_id: int, ilvl: int, quality: int,
          side: str = "buy") -> tuple[int, int] | None:
    """Return ``(price, price2)`` copper per single item, or None if the service
    doesn't cover it (204) or is unreachable. ``random_id`` is the item's signed
    randomPropertyId (positive = fixed property, negative = suffix).

    ``side="buy"`` (default for the guildhall) asks for the bot's BUY price -- what
    a player would receive selling the item to the AH bot, which is what this panel
    shows. ``side="sell"`` would ask for the list price instead."""
    qs = urllib.parse.urlencode({
        "entry": entry, "random": random_id, "ilvl": ilvl, "quality": quality,
        "side": side,
    })
    try:
        with urllib.request.urlopen(f"{URL}?{qs}", timeout=TIMEOUT) as resp:
            if resp.status == 204:
                return None
            body = resp.read().decode("utf-8").strip()
    except Exception:  # noqa: BLE001 -- any failure -> local fallback (bot behaviour)
        return None
    if not body:
        return None
    parts = body.split(",")
    try:
        buyout = int(parts[0])
        bid = int(parts[1]) if len(parts) > 1 else buyout
        multiplier = float(parts[2])
    except (ValueError, IndexError):
        return None
    return buyout, bid, multiplier
