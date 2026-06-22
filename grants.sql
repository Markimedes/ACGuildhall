-- Guildhall DB grants for BOTH connection sources.
--
-- The app reaches MySQL from two places with the same least-privilege account:
--   * 'guildhall'@'localhost'   -- running on the host directly (python app.py)
--   * 'guildhall'@'172.19.%'    -- the container on the Docker 'edge' subnet
--                                  (172.19.0.0/16; match to your network)
-- This file grants the identical, complete privilege set to each so either path
-- works. It is idempotent -- safe to re-run after adding tables or a host.
--
-- Run as a user that can CREATE USER / GRANT (e.g. root):
--     sudo mysql < grants.sql
--
-- Privileges only; it does NOT create the schema. Run schema.sql first (fresh
-- install) or migrations/001_move_to_guildhall_db.sql (existing install).
--
-- Set a real password out-of-band to keep secrets out of git, e.g.:
--   sudo mysql -e "ALTER USER 'guildhall'@'localhost' IDENTIFIED BY '<secret>';"
--   sudo mysql -e "ALTER USER 'guildhall'@'172.19.%'  IDENTIFIED BY '<secret>';"

CREATE DATABASE IF NOT EXISTS guildhall
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'guildhall'@'localhost' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
CREATE USER IF NOT EXISTS 'guildhall'@'172.19.%'  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- ===========================================================================
-- 'guildhall'@'localhost'
-- ===========================================================================
-- The app fully owns its own database.
GRANT SELECT, INSERT, UPDATE, DELETE ON guildhall.* TO 'guildhall'@'localhost';
-- acore_auth: read identity, update salt/verifier (password change), insert new
-- accounts (invite registration), read realm + GM level.
GRANT SELECT (id, username, salt, verifier) ON acore_auth.account TO 'guildhall'@'localhost';
GRANT UPDATE (salt, verifier)               ON acore_auth.account TO 'guildhall'@'localhost';
GRANT INSERT (username, salt, verifier, expansion, reg_mail, email) ON acore_auth.account TO 'guildhall'@'localhost';
GRANT SELECT, INSERT ON acore_auth.realmcharacters TO 'guildhall'@'localhost';
GRANT SELECT          ON acore_auth.realmlist       TO 'guildhall'@'localhost';
GRANT SELECT (id, gmlevel) ON acore_auth.account_access TO 'guildhall'@'localhost';
-- acore_characters: read characters/skills/guild + held items (public game data).
GRANT SELECT ON acore_characters.characters         TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_skills   TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_spell    TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.guild              TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.guild_member       TO 'guildhall'@'localhost';
GRANT SELECT (guid, owner_guid, itemEntry, count, flags, randomPropertyId) ON acore_characters.item_instance TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_inventory TO 'guildhall'@'localhost';
-- Heroic Exploits news: earned achievements + completed quests.
GRANT SELECT ON acore_characters.character_achievement          TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_queststatus_rewarded TO 'guildhall'@'localhost';
-- acore_world: live Auction House pricing + quest name resolution. (Achievement
-- names come from the client DBC via achievements.json, not MySQL.)
GRANT SELECT ON acore_world.item_template   TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.npc_vendor      TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.quest_template  TO 'guildhall'@'localhost';
-- acore_chronicle: per-character life-event timeline (mod-chronicle). Also
-- granted by modules/mod-chronicle/sql/mod_chronicle_setup.sql; keep in sync.
GRANT SELECT ON acore_chronicle.character_chronicle TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.creature_queststarter TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.creature_questender TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.creature_template TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.quest_template_addon TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.game_tele TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.creature TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.factiontemplate_dbc TO 'guildhall'@'localhost';
GRANT SELECT ON acore_world.spell_dbc TO 'guildhall'@'localhost';

-- ===========================================================================
-- 'guildhall'@'172.19.%'   (Docker 'edge' subnet)
-- ===========================================================================
GRANT SELECT, INSERT, UPDATE, DELETE ON guildhall.* TO 'guildhall'@'172.19.%';
GRANT SELECT (id, username, salt, verifier) ON acore_auth.account TO 'guildhall'@'172.19.%';
GRANT UPDATE (salt, verifier)               ON acore_auth.account TO 'guildhall'@'172.19.%';
GRANT INSERT (username, salt, verifier, expansion, reg_mail, email) ON acore_auth.account TO 'guildhall'@'172.19.%';
GRANT SELECT, INSERT ON acore_auth.realmcharacters TO 'guildhall'@'172.19.%';
GRANT SELECT          ON acore_auth.realmlist       TO 'guildhall'@'172.19.%';
GRANT SELECT (id, gmlevel) ON acore_auth.account_access TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.characters         TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_skills   TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_spell    TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.guild              TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.guild_member       TO 'guildhall'@'172.19.%';
GRANT SELECT (guid, owner_guid, itemEntry, count, flags, randomPropertyId) ON acore_characters.item_instance TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_inventory TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_achievement          TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_queststatus_rewarded TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.item_template   TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.npc_vendor      TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.quest_template  TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_chronicle.character_chronicle TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.creature_queststarter TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.creature_questender TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.creature_template TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.creature TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.game_tele TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.game_tele TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.quest_template_addon TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.factiontemplate_dbc TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.spell_dbc TO 'guildhall'@'172.19.%';

FLUSH PRIVILEGES;
