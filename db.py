"""Data access for Guildhall.

A single connection pool (the least-privilege ``guildhall`` user) serves every
schema; each table is schema-qualified so no default database is needed. The app's
own tables live in the ``guildhall`` database; it also reads AzerothCore data from
``acore_auth.*`` / ``acore_characters.*`` / ``acore_world.*``. All queries are
parameterized.
"""

from __future__ import annotations

from typing import Any, Optional

import mysql.connector
from mysql.connector import pooling

from professions import PROFESSION_SKILL_IDS

_pool: Optional[pooling.MySQLConnectionPool] = None


def init_pool(cfg: dict) -> None:
    """Create the shared connection pool from the [database] config section."""
    global _pool
    _pool = pooling.MySQLConnectionPool(
        pool_name="guildhall",
        pool_size=cfg.get("pool_size", 5),
        host=cfg["host"],
        port=cfg.get("port", 3306),
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=True,
    )


def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = _pool.get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def _query_one(sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    rows = _query(sql, params)
    return rows[0] if rows else None


def _execute(sql: str, params: tuple = ()) -> int:
    """Run a write; return lastrowid (for INSERT) or affected rowcount."""
    conn = _pool.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = cur.lastrowid or cur.rowcount
        cur.close()
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Accounts (acore_auth)
# ---------------------------------------------------------------------------
def get_account_by_username(username: str) -> Optional[dict[str, Any]]:
    return _query_one(
        "SELECT id, username, salt, verifier FROM acore_auth.account "
        "WHERE username = %s",
        (username,),
    )


def get_account_by_id(account_id: int) -> Optional[dict[str, Any]]:
    return _query_one(
        "SELECT id, username, salt, verifier FROM acore_auth.account "
        "WHERE id = %s",
        (account_id,),
    )


def update_password(account_id: int, salt: bytes, verifier: bytes) -> None:
    _execute(
        "UPDATE acore_auth.account SET salt = %s, verifier = %s WHERE id = %s",
        (salt, verifier, account_id),
    )


# ---------------------------------------------------------------------------
# Guild identity (acore_characters)
# ---------------------------------------------------------------------------
def resolve_guild(account_id: int) -> Optional[dict[str, Any]]:
    """Resolve the account's guild context from its highest-level guilded char.

    Returns ``{guildid, guild_name, char_guid, char_name, is_leader}`` or None
    if none of the account's characters are in a guild. ``char_guid`` is the
    representative character used as ``author_guid`` for posts. ``is_leader`` is
    true when the account owns the character that is the guild's leaderguid.
    """
    row = _query_one(
        "SELECT c.guid AS char_guid, c.name AS char_name, "
        "       gm.guildid AS guildid, g.name AS guild_name, "
        "       g.leaderguid AS leaderguid "
        "FROM acore_characters.characters c "
        "JOIN acore_characters.guild_member gm ON gm.guid = c.guid "
        "JOIN acore_characters.guild g ON g.guildid = gm.guildid "
        "WHERE c.account = %s "
        "ORDER BY c.level DESC, c.guid ASC LIMIT 1",
        (account_id,),
    )
    if not row:
        return None

    owns_leader = _query_one(
        "SELECT 1 AS ok FROM acore_characters.characters "
        "WHERE account = %s AND guid = %s",
        (account_id, row["leaderguid"]),
    )
    return {
        "guildid": row["guildid"],
        "guild_name": row["guild_name"],
        "char_guid": row["char_guid"],
        "char_name": row["char_name"],
        "is_leader": owns_leader is not None,
    }


# ---------------------------------------------------------------------------
# Forum (custom tables)
# ---------------------------------------------------------------------------
def list_feed(guildid: int, feed: str) -> list[dict[str, Any]]:
    return _query(
        "SELECT p.id, p.title, p.author_guid, p.created_at, "
        "       c.name AS author_name, "
        "       (SELECT COUNT(*) FROM guildhall.guild_post_reply r "
        "        WHERE r.post_id = p.id) AS reply_count "
        "FROM guildhall.guild_post p "
        "LEFT JOIN acore_characters.characters c ON c.guid = p.author_guid "
        "WHERE p.guildid = %s AND p.feed = %s "
        "ORDER BY p.created_at DESC, p.id DESC",
        (guildid, feed),
    )


def feed_counts(guildid: int) -> dict[str, int]:
    rows = _query(
        "SELECT feed, COUNT(*) AS n FROM guildhall.guild_post "
        "WHERE guildid = %s GROUP BY feed",
        (guildid,),
    )
    counts = {"official": 0, "player": 0}
    for r in rows:
        counts[r["feed"]] = r["n"]
    return counts


def get_post(post_id: int) -> Optional[dict[str, Any]]:
    return _query_one(
        "SELECT p.id, p.guildid, p.feed, p.author_guid, p.title, p.body, "
        "       p.created_at, c.name AS author_name "
        "FROM guildhall.guild_post p "
        "LEFT JOIN acore_characters.characters c ON c.guid = p.author_guid "
        "WHERE p.id = %s",
        (post_id,),
    )


def list_replies(post_id: int) -> list[dict[str, Any]]:
    return _query(
        "SELECT r.id, r.author_guid, r.body, r.created_at, "
        "       c.name AS author_name "
        "FROM guildhall.guild_post_reply r "
        "LEFT JOIN acore_characters.characters c ON c.guid = r.author_guid "
        "WHERE r.post_id = %s "
        "ORDER BY r.created_at ASC, r.id ASC",
        (post_id,),
    )


def get_reply(reply_id: int) -> Optional[dict[str, Any]]:
    """Reply joined to its post's guildid, for ownership/guild checks."""
    return _query_one(
        "SELECT r.id, r.author_guid, r.post_id, p.guildid "
        "FROM guildhall.guild_post_reply r "
        "JOIN guildhall.guild_post p ON p.id = r.post_id "
        "WHERE r.id = %s",
        (reply_id,),
    )


def create_post(
    guildid: int, feed: str, author_guid: int, title: str, body: str
) -> int:
    return _execute(
        "INSERT INTO guildhall.guild_post "
        "(guildid, feed, author_guid, title, body) VALUES (%s, %s, %s, %s, %s)",
        (guildid, feed, author_guid, title, body),
    )


def create_reply(post_id: int, author_guid: int, body: str) -> int:
    return _execute(
        "INSERT INTO guildhall.guild_post_reply "
        "(post_id, author_guid, body) VALUES (%s, %s, %s)",
        (post_id, author_guid, body),
    )


def delete_post(post_id: int) -> None:
    # Replies cascade via the FK.
    _execute("DELETE FROM guildhall.guild_post WHERE id = %s", (post_id,))


def delete_reply(reply_id: int) -> None:
    _execute(
        "DELETE FROM guildhall.guild_post_reply WHERE id = %s",
        (reply_id,),
    )


# ---------------------------------------------------------------------------
# Profession roster
# ---------------------------------------------------------------------------
def guild_professions(guildid: int) -> list[dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(PROFESSION_SKILL_IDS))
    return _query(
        "SELECT c.guid AS char_guid, c.name AS char_name, c.level, "
        "       cs.skill, cs.value, cs.max "
        "FROM acore_characters.guild_member gm "
        "JOIN acore_characters.characters c ON c.guid = gm.guid "
        "JOIN acore_characters.character_skills cs ON cs.guid = c.guid "
        f"WHERE gm.guildid = %s AND cs.skill IN ({placeholders}) "
        "ORDER BY c.name ASC, cs.skill ASC",
        (guildid, *PROFESSION_SKILL_IDS),
    )


def guild_profession_masters(guildid: int) -> list[dict[str, Any]]:
    """For each profession present in the guild, the member with the highest
    skill value (tie-break: higher level, then lowest guid)."""
    placeholders = ", ".join(["%s"] * len(PROFESSION_SKILL_IDS))
    return _query(
        "SELECT skill, char_guid, char_name, value, max FROM ("
        "  SELECT cs.skill AS skill, c.guid AS char_guid, c.name AS char_name, "
        "         cs.value AS value, cs.max AS max, "
        "         ROW_NUMBER() OVER (PARTITION BY cs.skill "
        "           ORDER BY cs.value DESC, c.level DESC, c.guid ASC) AS rn "
        "  FROM acore_characters.guild_member gm "
        "  JOIN acore_characters.characters c ON c.guid = gm.guid "
        "  JOIN acore_characters.character_skills cs ON cs.guid = c.guid "
        f"  WHERE gm.guildid = %s AND cs.skill IN ({placeholders})"
        ") t WHERE rn = 1 ORDER BY skill",
        (guildid, *PROFESSION_SKILL_IDS),
    )


def guild_member_char(guid: int) -> Optional[dict[str, Any]]:
    """A character's guild membership + brief, or None if not in any guild.
    Used to guild-scope the player detail page."""
    return _query_one(
        "SELECT c.guid, c.name, c.level, c.race, c.class, gm.guildid "
        "FROM acore_characters.characters c "
        "JOIN acore_characters.guild_member gm ON gm.guid = c.guid "
        "WHERE c.guid = %s",
        (guid,),
    )


def account_characters(account_id: int) -> list[dict[str, Any]]:
    """All of an account's characters with guild info (NULL guildid if none),
    highest level first. Used for the character switcher. ``online``/``money``/
    ``race`` support the Auction House listing flow (online gating, fee preview,
    faction-specific house)."""
    return _query(
        "SELECT c.guid, c.name, c.level, c.online, c.money, c.race, gm.guildid, "
        "       g.name AS guild_name, g.leaderguid "
        "FROM acore_characters.characters c "
        "LEFT JOIN acore_characters.guild_member gm ON gm.guid = c.guid "
        "LEFT JOIN acore_characters.guild g ON g.guildid = gm.guildid "
        "WHERE c.account = %s "
        "ORDER BY c.level DESC, c.guid ASC",
        (account_id,),
    )


def item_sell_prices(entries) -> dict[int, int]:
    """Vendor SellPrice (copper) per item entry, for the auction deposit estimate.
    The worldserver computes the authoritative deposit; this is only a preview."""
    entries = list(entries)
    if not entries:
        return {}
    placeholders = ", ".join(["%s"] * len(entries))
    rows = _query(
        "SELECT entry, SellPrice FROM acore_world.item_template "
        f"WHERE entry IN ({placeholders})",
        tuple(entries),
    )
    return {r["entry"]: int(r["SellPrice"]) for r in rows}


def item_max_stacks(entries) -> dict[int, int]:
    """Max stack size per item entry (item_template.stackable), for the auction
    stack-size control. A value < 1 means non-stackable; treat as 1."""
    entries = list(entries)
    if not entries:
        return {}
    placeholders = ", ".join(["%s"] * len(entries))
    rows = _query(
        "SELECT entry, stackable FROM acore_world.item_template "
        f"WHERE entry IN ({placeholders})",
        tuple(entries),
    )
    return {r["entry"]: max(1, int(r["stackable"])) for r in rows}


def held_item_instances(guid: int) -> dict[int, dict[str, Any]]:
    """Map of item_guid -> {itemEntry, count, flags, randomPropertyId} for every
    item the character currently holds (bags + bank), used to re-validate a web
    listing selection against live inventory before sending it to the server."""
    rows = _query(
        "SELECT ci.item AS item_guid, ii.itemEntry, ii.count, ii.flags, "
        "       ii.randomPropertyId "
        "FROM acore_characters.character_inventory ci "
        "JOIN acore_characters.item_instance ii ON ii.guid = ci.item "
        "WHERE ci.guid = %s",
        (guid,),
    )
    return {r["item_guid"]: r for r in rows}


def character_professions(guid: int) -> list[dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(PROFESSION_SKILL_IDS))
    return _query(
        "SELECT skill, value, max FROM acore_characters.character_skills "
        f"WHERE guid = %s AND skill IN ({placeholders}) ORDER BY skill",
        (guid, *PROFESSION_SKILL_IDS),
    )


def guild_names(guids) -> dict[int, str]:
    """Map character guids -> their guild name (omitting the guildless)."""
    guids = [int(g) for g in guids]
    if not guids:
        return {}
    ph = ", ".join(["%s"] * len(guids))
    rows = _query(
        "SELECT gm.guid, g.name AS guild_name "
        "FROM acore_characters.guild_member gm "
        "JOIN acore_characters.guild g ON g.guildid = gm.guildid "
        f"WHERE gm.guid IN ({ph})",
        tuple(guids),
    )
    return {r["guid"]: r["guild_name"] for r in rows}


def character_known_spell_ids(guid: int) -> list[int]:
    rows = _query(
        "SELECT spell FROM acore_characters.character_spell WHERE guid = %s",
        (guid,),
    )
    return [r["spell"] for r in rows]


def character_held_items(guid: int) -> set[int]:
    """Distinct item entries the character currently holds (bags + bank)."""
    rows = _query(
        "SELECT DISTINCT itemEntry FROM acore_characters.item_instance "
        "WHERE owner_guid = %s",
        (guid,),
    )
    return {r["itemEntry"] for r in rows}


def character_inventory_breakdown(guid: int) -> list[dict[str, Any]]:
    """Every item the character holds, with its container position, stack count and
    per-instance flags (bit 0x1 = soulbound). Joins character_inventory to
    item_instance so each row carries where the item sits (bag/slot), letting the
    caller split backpack/bags (inventory) from bank and skip equipped gear.

    ``item_guid`` is the item-instance id (used to resolve nested bag contents:
    a nested row's ``bag`` equals its parent container's ``item_guid``).
    ``randomPropertyId`` is the rolled suffix/property (signed) for AH pricing."""
    return _query(
        "SELECT ci.bag, ci.slot, ci.item AS item_guid, "
        "       ii.itemEntry, ii.count, ii.flags, ii.randomPropertyId "
        "FROM acore_characters.character_inventory ci "
        "JOIN acore_characters.item_instance ii ON ii.guid = ci.item "
        "WHERE ci.guid = %s",
        (guid,),
    )


def item_display_for(entries) -> list[dict[str, Any]]:
    """Display fields (name, icon source, quality, item level) for the given item
    entries, from acore_world. Pricing itself comes from the AHPricingService."""
    entries = list(entries)
    if not entries:
        return []
    placeholders = ", ".join(["%s"] * len(entries))
    return _query(
        "SELECT entry, name, displayid, Quality, ItemLevel "
        f"FROM acore_world.item_template WHERE entry IN ({placeholders})",
        tuple(entries),
    )


def character_item_counts(guid: int, item_ids) -> dict[int, int]:
    """Total quantity of each item the character holds (bags + bank + equipped),
    summed from item_instance. Returns {itemEntry: count} for the wanted ids."""
    item_ids = list(item_ids)
    if not item_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(item_ids))
    rows = _query(
        "SELECT itemEntry, SUM(count) AS qty FROM acore_characters.item_instance "
        f"WHERE owner_guid = %s AND itemEntry IN ({placeholders}) "
        "GROUP BY itemEntry",
        (guid, *item_ids),
    )
    return {r["itemEntry"]: int(r["qty"]) for r in rows}


# --- in-demand resources cache (lazy, TTL-refreshed) -----------------------
def demand_get(guid: int) -> Optional[dict[str, Any]]:
    """Cached demand row with age in minutes, or None."""
    return _query_one(
        "SELECT items, computed_at, "
        "       TIMESTAMPDIFF(MINUTE, computed_at, NOW()) AS age_minutes "
        "FROM guildhall.player_demand WHERE guid = %s",
        (guid,),
    )


def demand_store(guid: int, items_json: str) -> None:
    _execute(
        "INSERT INTO guildhall.player_demand (guid, items, computed_at) "
        "VALUES (%s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE items = VALUES(items), computed_at = NOW()",
        (guid, items_json),
    )


# --- AI news desk article cache (one row per category per event-day) -------
def news_get(event_date: str) -> list[dict[str, Any]]:
    """All cached articles for a market-event date (any categories present)."""
    return _query(
        "SELECT category, headline, dek, content, author, author_title, "
        "       dateline, created_at "
        "FROM guildhall.news WHERE event_date = %s "
        "ORDER BY category",
        (event_date,),
    )


def edition_dates(limit: int = 60) -> list[str]:
    """The distinct dates that have a published edition -- a market story or a
    Heroic Exploits/Obituary -- newest first, as ISO ``YYYY-MM-DD`` strings.
    Drives the News archive's prev/next navigation."""
    rows = _query(
        "SELECT d FROM ("
        "  SELECT event_date AS d FROM guildhall.news"
        "  UNION"
        "  SELECT edition_date AS d FROM guildhall.exploit_news"
        ") x ORDER BY d DESC LIMIT %s",
        (int(limit),),
    )
    return [r["d"].isoformat() for r in rows]


def news_store(event_date: str, article: dict[str, Any]) -> None:
    """Upsert one generated article for ``event_date`` (keyed by its category)."""
    _execute(
        "INSERT INTO guildhall.news "
        "(category, event_date, headline, dek, content, author, "
        " author_title, dateline) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE headline = VALUES(headline), "
        "dek = VALUES(dek), content = VALUES(content), author = VALUES(author), "
        "author_title = VALUES(author_title), dateline = VALUES(dateline)",
        (article["category"], event_date, article["headline"], article["dek"],
         article["content"], article["author"], article["author_title"],
         article["dateline"]),
    )


def news_clear(event_date: str) -> int:
    """Delete cached market stories for an event date so they regenerate.
    Returns the number of rows removed."""
    return _execute(
        "DELETE FROM guildhall.news WHERE event_date = %s", (event_date,))


# --- Heroic Exploits: player activity (achievements + quests) --------------
def chronicle_for_edition(edition_date: str) -> list[dict[str, Any]]:
    """Every chronicle event for the day an edition reports, oldest-first within
    each character. A daily paper is generated just after midnight and covers the
    day that just ended, so edition ``D`` spans the calendar day ``[D-1, D)``.

    Bounding by the precise ``event_time`` (not a rolling NOW-window) means every
    event belongs to exactly ONE edition -- so a deed is reported once and never
    re-surfaces in a later edition on a restart or a re-run. Display names are
    pre-resolved in ``detail``; this is the single feed the Heroic Exploits /
    Obituaries desk works from."""
    return _query(
        "SELECT guid, event_time, event_type, level, map_id, zone_id, area_id, "
        "       ref_id, ref_id2, value, detail "
        "FROM acore_chronicle.character_chronicle "
        "WHERE event_time >= %s - INTERVAL 1 DAY AND event_time < %s "
        "ORDER BY guid, id",
        (edition_date, edition_date),
    )


def characters_by_guids(guids) -> dict[int, dict[str, Any]]:
    """Identity fields (name/race/class/level) for ``guids`` -> ``{guid: row}``.
    Used to put a face to the guids that turn up in the chronicle window."""
    guids = [int(g) for g in guids]
    if not guids:
        return {}
    ph = ", ".join(["%s"] * len(guids))
    rows = _query(
        "SELECT guid, name, race, class, level "
        f"FROM acore_characters.characters WHERE guid IN ({ph})",
        tuple(guids),
    )
    return {r["guid"]: r for r in rows}


def chronicle_events(guid: int, limit: int = 200) -> list[dict[str, Any]]:
    """Recent life events for a character, newest first, from the append-only
    acore_chronicle log written by the mod-chronicle worldserver module. Display
    names are precomputed in the ``detail`` column, so no joins are needed; item
    and achievement rows still carry ``ref_id`` for wowhead links."""
    return _query(
        "SELECT id, event_time, event_type, level, map_id, zone_id, area_id, "
        "       ref_id, ref_id2, value, detail "
        "FROM acore_chronicle.character_chronicle "
        "WHERE guid = %s "
        "ORDER BY id DESC "
        "LIMIT %s",
        (guid, int(limit)),
    )


# --- Heroic Exploits / Obituaries: generated-article cache (one row per story
# per day). Story keys namespace the kind: c<guid> individual exploit,
# g<q|b><refid> group exploit, d<guid> obituary (see exploits.py). ---
def exploit_news_get(edition_date: str) -> list[dict[str, Any]]:
    """All cached exploit stories for an edition date."""
    return _query(
        "SELECT story_key, subject, headline, dek, content, author, "
        "       author_title, dateline, created_at "
        "FROM guildhall.exploit_news WHERE edition_date = %s "
        "ORDER BY created_at",
        (edition_date,),
    )


def exploit_news_store(edition_date: str, story_key: str, subject: str,
                       article: dict[str, Any]) -> None:
    """Upsert one generated exploit story (keyed by edition_date + story_key)."""
    _execute(
        "INSERT INTO guildhall.exploit_news "
        "(edition_date, story_key, subject, headline, dek, content, author, "
        " author_title, dateline) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE subject = VALUES(subject), "
        "headline = VALUES(headline), dek = VALUES(dek), "
        "content = VALUES(content), author = VALUES(author), "
        "author_title = VALUES(author_title), dateline = VALUES(dateline)",
        (edition_date, story_key, subject, article["headline"], article["dek"],
         article["content"], article["author"], article["author_title"],
         article["dateline"]),
    )


def exploit_news_clear(edition_date: str, kind: str = "all") -> int:
    """Delete cached Heroic Exploits / Obituaries for an edition so they
    regenerate. ``kind`` is 'all', 'exploits' (heroic exploits only) or
    'obituaries' (story keys are namespaced -- 'd...' is an obituary; see
    exploits.OBITUARY_PREFIX). Returns the number of rows removed."""
    if kind == "obituaries":
        return _execute(
            "DELETE FROM guildhall.exploit_news "
            "WHERE edition_date = %s AND story_key LIKE 'd%%'", (edition_date,))
    if kind == "exploits":
        return _execute(
            "DELETE FROM guildhall.exploit_news "
            "WHERE edition_date = %s AND story_key NOT LIKE 'd%%'",
            (edition_date,))
    return _execute(
        "DELETE FROM guildhall.exploit_news WHERE edition_date = %s",
        (edition_date,))


# ---------------------------------------------------------------------------
# Heroic Exploits enrichment: world/reference lookups (read-only acore_world).
# These put names, quests, locations and gear behind the bare ids the chronicle
# records, so the desk can write richer copy. areatable_dbc is not populated on
# this realm, so place names come from the nearest game_tele point instead.
# ---------------------------------------------------------------------------
def spell_names(spell_ids) -> dict[int, dict[str, Any]]:
    """{spell_id: {name, level}} from Spell.dbc for the given ids."""
    ids = [int(s) for s in spell_ids if s]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        "SELECT ID, Name_Lang_enUS AS name, SpellLevel AS level "
        f"FROM acore_world.spell_dbc WHERE ID IN ({ph})",
        tuple(ids),
    )
    return {r["ID"]: r for r in rows if r.get("name")}


def creature_names(entries) -> dict[int, dict[str, Any]]:
    """{entry: {name, subname, rank, type, minlevel, maxlevel}} from
    creature_template. Level + rank let the desk gauge a foe's importance (an
    XP analog, since kill XP is a core formula, not a DB value)."""
    ids = [int(e) for e in entries if e]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        "SELECT entry, name, subname, `rank`, `type`, minlevel, maxlevel "
        f"FROM acore_world.creature_template WHERE entry IN ({ph})",
        tuple(ids),
    )
    return {r["entry"]: r for r in rows}


def item_names(entries) -> dict[int, str]:
    """{item_entry: name} from item_template."""
    ids = [int(e) for e in entries if e]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        f"SELECT entry, name FROM acore_world.item_template WHERE entry IN ({ph})",
        tuple(ids),
    )
    return {r["entry"]: r["name"] for r in rows}


def quest_details(quest_ids) -> dict[int, dict[str, Any]]:
    """Raw quest_template rows (title, description, objective text and the
    kill/collect requirements) keyed by quest id. Names behind the requirement
    ids are resolved by the caller via creature_names / item_names."""
    ids = [int(q) for q in quest_ids if q]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        "SELECT ID, LogTitle, LogDescription, QuestDescription, "
        "       AreaDescription, QuestLevel, RewardXPDifficulty, StartItem, "
        "       RewardItem1, RewardSpell, RewardTitle, "
        "       RequiredNpcOrGo1, RequiredNpcOrGo2, RequiredNpcOrGo3, "
        "       RequiredNpcOrGo4, RequiredNpcOrGoCount1, RequiredNpcOrGoCount2, "
        "       RequiredNpcOrGoCount3, RequiredNpcOrGoCount4, "
        "       RequiredItemId1, RequiredItemId2, RequiredItemId3, "
        "       RequiredItemId4, RequiredItemId5, RequiredItemId6, "
        "       RequiredItemCount1, RequiredItemCount2, RequiredItemCount3, "
        "       RequiredItemCount4, RequiredItemCount5, RequiredItemCount6 "
        f"FROM acore_world.quest_template WHERE ID IN ({ph})",
        tuple(ids),
    )
    return {r["ID"]: r for r in rows}


def quest_chain_links(quest_ids) -> dict[int, dict[str, int]]:
    """{quest_id: {prev, next}} from quest_template_addon -- the previous and
    next quest in a chain (0 when there is none). Used to walk a whole chain."""
    ids = [int(q) for q in quest_ids if q]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        "SELECT ID, PrevQuestID AS prev, NextQuestID AS next "
        f"FROM acore_world.quest_template_addon WHERE ID IN ({ph})",
        tuple(ids),
    )
    return {r["ID"]: {"prev": r["prev"] or 0, "next": r["next"] or 0}
            for r in rows}


def quest_titles(quest_ids) -> dict[int, str]:
    """{quest_id: LogTitle} -- a light lookup for naming chain members."""
    ids = [int(q) for q in quest_ids if q]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        f"SELECT ID, LogTitle FROM acore_world.quest_template WHERE ID IN ({ph})",
        tuple(ids),
    )
    return {r["ID"]: r["LogTitle"] for r in rows}


def quest_giver_enders(quest_ids) -> dict[int, dict[str, list[int]]]:
    """{quest_id: {"starters": [creature_entry...], "enders": [...]}}.
    The NPCs who hand out and take in each quest."""
    ids = [int(q) for q in quest_ids if q]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    out: dict[int, dict[str, list[int]]] = {
        q: {"starters": [], "enders": []} for q in ids}
    for table, key in (("creature_queststarter", "starters"),
                       ("creature_questender", "enders")):
        for r in _query(
            f"SELECT quest, id FROM acore_world.{table} WHERE quest IN ({ph})",
            tuple(ids),
        ):
            out.setdefault(r["quest"],
                           {"starters": [], "enders": []})[key].append(r["id"])
    return out


def creature_spawns(entries) -> dict[int, dict[str, Any]]:
    """{creature_entry: {map, x, y, z}} -- one representative spawn per entry
    (the lowest-guid spawn), used to anchor a location and find nearby NPCs."""
    ids = [int(e) for e in entries if e]
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    rows = _query(
        "SELECT id1, map, position_x AS x, position_y AS y, position_z AS z "
        f"FROM acore_world.creature WHERE id1 IN ({ph}) ORDER BY id1",
        tuple(ids),
    )
    out: dict[int, dict[str, Any]] = {}
    for r in rows:  # first row per entry wins (ORDER BY keeps it stable)
        out.setdefault(r["id1"], {"map": r["map"], "x": r["x"],
                                  "y": r["y"], "z": r["z"]})
    return out


def nearest_place(map_id: int, x: float, y: float) -> Optional[str]:
    """The name of the closest game_tele point on the same map -- a recognisable
    landmark to stand in for a zone name (areatable_dbc is empty on this realm).
    Returns the raw CamelCase tele key; the caller prettifies it."""
    row = _query_one(
        "SELECT name FROM acore_world.game_tele WHERE map = %s "
        "ORDER BY POW(position_x - %s, 2) + POW(position_y - %s, 2) ASC LIMIT 1",
        (int(map_id), float(x), float(y)),
    )
    return row["name"] if row else None


def nearby_creature_names(map_id: int, x: float, y: float, radius: float,
                          limit: int, team_mask: int,
                          exclude_entry: int = 0) -> list[str]:
    """Up to ``limit`` distinct ALLIED named NPCs spawned within ``radius`` yards
    of (x, y) on ``map_id``, nearest first -- the friendly bystanders who would
    have witnessed the event. ``team_mask`` is the deed-doer's faction mask
    (PLAYER|ALLIANCE=3 or PLAYER|HORDE=5); only creatures whose faction template
    is friendly to that mask and not hostile to it count, which drops enemy mobs
    and the opposing faction. Also excludes critters (type 8), the anchor
    creature itself and unnamed triggers/dummies."""
    lo_x, hi_x = float(x) - radius, float(x) + radius
    lo_y, hi_y = float(y) - radius, float(y) + radius
    rows = _query(
        "SELECT ct.name AS name, "
        "       MIN(POW(c.position_x - %s, 2) + POW(c.position_y - %s, 2)) AS d "
        "FROM acore_world.creature c "
        "JOIN acore_world.creature_template ct ON ct.entry = c.id1 "
        "JOIN acore_world.factiontemplate_dbc ft ON ft.ID = ct.faction "
        "WHERE c.map = %s AND c.position_x BETWEEN %s AND %s "
        "  AND c.position_y BETWEEN %s AND %s "
        "  AND ct.`type` <> 8 AND ct.name <> '' AND c.id1 <> %s "
        "  AND ct.name NOT LIKE '[%%' AND ct.name NOT LIKE '%%Dummy%%' "
        "  AND (ft.FriendGroup & %s) <> 0 AND (ft.EnemyGroup & %s) = 0 "
        "GROUP BY ct.name HAVING d <= %s ORDER BY d ASC LIMIT %s",
        (float(x), float(y), int(map_id), lo_x, hi_x, lo_y, hi_y,
         int(exclude_entry), int(team_mask), int(team_mask),
         radius * radius, int(limit)),
    )
    return [r["name"] for r in rows]


def character_equipped_weapons(guid: int) -> list[dict[str, Any]]:
    """The weapons the character has equipped (main hand, off hand, ranged),
    with item subclass + quality, in slot order. Equipment lives in
    character_inventory bag 0, slots 15/16/17."""
    rows = _query(
        "SELECT ci.slot AS slot, it.name AS name, it.subclass AS subclass, "
        "       it.Quality AS quality, it.ItemLevel AS ilvl "
        "FROM acore_characters.character_inventory ci "
        "JOIN acore_characters.item_instance ii ON ii.guid = ci.item "
        "JOIN acore_world.item_template it ON it.entry = ii.itemEntry "
        "WHERE ci.guid = %s AND ci.bag = 0 AND ci.slot IN (15, 16, 17) "
        "  AND it.class = 2 "
        "ORDER BY ci.slot",
        (guid,),
    )
    return rows


def character_weapon_skills(guid: int) -> list[dict[str, Any]]:
    """The character's weapon proficiencies {skill, value, max}, highest value
    first -- how practiced they are with each weapon family. The caller maps the
    skill id to a name (weapons.WEAPON_SKILL) and drops non-weapon lines."""
    return _query(
        "SELECT skill, value, max FROM acore_characters.character_skills "
        "WHERE guid = %s AND value > 1 ORDER BY value DESC",
        (guid,),
    )


def character_known_spells(guid: int, limit: int = 80) -> list[dict[str, Any]]:
    """The character's most advanced known spells {spell, name, level}: active
    (non-passive) abilities that carry a name, highest spell level first. The
    caller dedupes by name and trims to the signature few."""
    return _query(
        "SELECT cs.spell AS spell, sd.Name_Lang_enUS AS name, "
        "       sd.SpellLevel AS level "
        "FROM acore_characters.character_spell cs "
        "JOIN acore_world.spell_dbc sd ON sd.ID = cs.spell "
        "WHERE cs.guid = %s AND sd.Name_Lang_enUS <> '' "
        "  AND (sd.Attributes & 64) = 0 "
        "ORDER BY sd.SpellLevel DESC, cs.spell DESC LIMIT %s",
        (guid, int(limit)),
    )


# ---------------------------------------------------------------------------
# Invites (acore_auth) + account creation
# ---------------------------------------------------------------------------
class InviteError(Exception):
    """Raised by redeem_invite_and_create_account. ``code`` is 'invalid' (link
    bad/expired/used) or 'taken' (username already exists)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def invite_available_tokens(account_id: int, default_tokens: int) -> dict[str, int]:
    """Return {allowance, consumed, available}. A token is consumed only while an
    invite is used or still pending+unexpired -- expired-unused invites refund."""
    row = _query_one(
        "SELECT tokens FROM guildhall.invite_allowance WHERE account_id = %s",
        (account_id,),
    )
    allowance = row["tokens"] if row else default_tokens
    consumed = _query_one(
        "SELECT COUNT(*) AS n FROM guildhall.invite "
        "WHERE inviter_account_id = %s "
        "AND (used_at IS NOT NULL OR expires_at > NOW())",
        (account_id,),
    )["n"]
    return {
        "allowance": allowance,
        "consumed": consumed,
        "available": max(0, allowance - consumed),
    }


def is_admin(account_id: int, min_gmlevel: int) -> bool:
    """True if the account has GM level >= min_gmlevel on any realm."""
    return _query_one(
        "SELECT 1 AS ok FROM acore_auth.account_access "
        "WHERE id = %s AND gmlevel >= %s LIMIT 1",
        (account_id, min_gmlevel),
    ) is not None


def account_id_by_username(username: str) -> Optional[int]:
    row = _query_one(
        "SELECT id FROM acore_auth.account WHERE username = %s", (username,)
    )
    return row["id"] if row else None


def allowance_set(account_id: int, tokens: int) -> None:
    _execute(
        "INSERT INTO guildhall.invite_allowance (account_id, tokens) "
        "VALUES (%s, %s) ON DUPLICATE KEY UPDATE tokens = VALUES(tokens)",
        (account_id, tokens),
    )


def allowance_remove(account_id: int) -> int:
    return _execute(
        "DELETE FROM guildhall.invite_allowance WHERE account_id = %s",
        (account_id,),
    )


def allowance_list() -> list[dict[str, Any]]:
    return _query(
        "SELECT al.account_id, a.username, al.tokens "
        "FROM guildhall.invite_allowance al "
        "JOIN acore_auth.account a ON a.id = al.account_id "
        "ORDER BY a.username",
    )


def invite_create(account_id: int, token_hash: bytes, ttl_hours: int) -> int:
    return _execute(
        "INSERT INTO guildhall.invite "
        "(token_hash, inviter_account_id, expires_at) "
        "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s HOUR))",
        (token_hash, account_id, ttl_hours),
    )


def invite_list_for(account_id: int) -> list[dict[str, Any]]:
    return _query(
        "SELECT id, created_at, expires_at, used_at, used_by_account_id, "
        "       (used_at IS NULL AND expires_at > NOW())  AS pending, "
        "       (used_at IS NULL AND expires_at <= NOW()) AS expired, "
        "       TIMESTAMPDIFF(MINUTE, NOW(), expires_at)  AS minutes_left "
        "FROM guildhall.invite "
        "WHERE inviter_account_id = %s "
        "ORDER BY created_at DESC",
        (account_id,),
    )


def invite_revoke(invite_id: int, account_id: int) -> int:
    """Delete the owner's own pending invite (immediate refund). Returns rowcount."""
    return _execute(
        "DELETE FROM guildhall.invite "
        "WHERE id = %s AND inviter_account_id = %s AND used_at IS NULL",
        (invite_id, account_id),
    )


def invite_is_redeemable(token_hash: bytes) -> bool:
    """Cheap read for the public GET page (the authoritative check happens inside
    the redeem transaction)."""
    row = _query_one(
        "SELECT 1 AS ok FROM guildhall.invite "
        "WHERE token_hash = %s AND used_at IS NULL AND expires_at > NOW()",
        (token_hash,),
    )
    return row is not None


def redeem_invite_and_create_account(
    token_hash: bytes, username: str, salt: bytes, verifier: bytes,
    expansion: int, email: str,
) -> int:
    """Atomically claim the invite and create the account. Mirrors
    AccountMgr::CreateAccount. Raises InviteError on a bad link or taken username."""
    conn = _pool.get_connection()
    try:
        conn.autocommit = False
        cur = conn.cursor(dictionary=True)
        # Lock the invite row for the duration of the transaction.
        cur.execute(
            "SELECT id, used_at, (expires_at > NOW()) AS unexpired "
            "FROM guildhall.invite WHERE token_hash = %s FOR UPDATE",
            (token_hash,),
        )
        inv = cur.fetchone()
        if not inv or inv["used_at"] is not None or not inv["unexpired"]:
            conn.rollback()
            raise InviteError("invalid")

        cur.execute(
            "SELECT id FROM acore_auth.account WHERE username = %s", (username,)
        )
        if cur.fetchone():
            conn.rollback()
            raise InviteError("taken")

        try:
            # joindate has DEFAULT CURRENT_TIMESTAMP, so we omit it (and avoid
            # needing a column grant for it).
            cur.execute(
                "INSERT INTO acore_auth.account "
                "(username, salt, verifier, expansion, reg_mail, email) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (username, salt, verifier, expansion, email, email),
            )
        except mysql.connector.IntegrityError:
            conn.rollback()  # UNIQUE(idx_username) race
            raise InviteError("taken")
        new_id = cur.lastrowid

        # Per-account realmcharacters init (mirrors LOGIN_INS_REALM_CHARACTERS_INIT).
        cur.execute(
            "INSERT IGNORE INTO acore_auth.realmcharacters (realmid, acctid, numchars) "
            "SELECT id, %s, 0 FROM acore_auth.realmlist",
            (new_id,),
        )
        cur.execute(
            "UPDATE guildhall.invite "
            "SET used_at = NOW(), used_by_account_id = %s WHERE id = %s",
            (new_id, inv["id"]),
        )
        conn.commit()
        return new_id
    finally:
        conn.close()
