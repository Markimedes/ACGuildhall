-- Guildhall schema & least-privilege DB user.
--
-- Run once against your MySQL server (as a user that can CREATE DATABASE /
-- CREATE USER / GRANT):
--     mysql -u root -p < schema.sql
--
-- The app's own tables live in their own `guildhall` database -- nothing of ours
-- is mixed into AzerothCore's acore_auth / acore_characters / acore_world schemas.
-- We only READ AzerothCore data (characters, guilds, accounts, items) and, for the
-- password-change and invite-registration features, write a few scoped columns on
-- acore_auth.account. Adjust the host portion of the 'guildhall'@'...' user and the
-- password below before running.
--
-- Migrating an existing install whose tables are still in acore_*? See
-- migrations/001_move_to_guildhall_db.sql (moves the data with RENAME TABLE).

-- ---------------------------------------------------------------------------
-- The Guildhall application database
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS guildhall
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Guild mini-forum: posts and threaded replies.
CREATE TABLE IF NOT EXISTS guildhall.guild_post (
  `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `guildid`     INT UNSIGNED NOT NULL,
  `feed`        ENUM('official','player') NOT NULL,
  `author_guid` INT UNSIGNED NOT NULL,
  `title`       VARCHAR(128) NOT NULL,
  `body`        VARCHAR(2000) NOT NULL,
  `created_at`  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `guild_feed_key` (`guildid`, `feed`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS guildhall.guild_post_reply (
  `id`          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `post_id`     INT UNSIGNED NOT NULL,
  `author_guid` INT UNSIGNED NOT NULL,
  `body`        VARCHAR(2000) NOT NULL,
  `created_at`  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `post_key` (`post_id`, `created_at`),
  CONSTRAINT `fk_reply_post` FOREIGN KEY (`post_id`)
    REFERENCES guildhall.guild_post (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Cached per-character "in-demand resources" (reagents needed for orange/yellow
-- recipes minus current bags+bank); refreshed lazily on a TTL.
CREATE TABLE IF NOT EXISTS guildhall.player_demand (
  `guid`        INT UNSIGNED NOT NULL,
  `computed_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `items`       JSON NOT NULL,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- AI news desk: one generated article per category per market-event day. The
-- daily AH events are deterministic per date, so we generate once and cache here
-- (first viewer of the day triggers generation; everyone after reads this row).
CREATE TABLE IF NOT EXISTS guildhall.news (
  `category`     VARCHAR(32)  NOT NULL,   -- professional_digest / gear_for_you / ...
  `event_date`   DATE         NOT NULL,   -- the market event's date (ahservice)
  `headline`     VARCHAR(255) NOT NULL,
  `dek`          VARCHAR(512) NOT NULL,
  `content`      TEXT         NOT NULL,
  `author`       VARCHAR(128) NOT NULL,
  `author_title` VARCHAR(128) NOT NULL,
  `dateline`     VARCHAR(64)  NOT NULL,
  `created_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`category`, `event_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- DEPRECATED (unused as of the chronicle migration): the per-character activity
-- snapshot that the old day-to-day diffing approach advanced. Heroic Exploits &
-- Obituaries now read acore_chronicle directly (see exploits.py), so nothing
-- writes or reads this table. Kept (not dropped) to avoid a destructive change;
-- safe to DROP once you're confident you won't roll back.
CREATE TABLE IF NOT EXISTS guildhall.character_activity (
  `guid`                  INT UNSIGNED NOT NULL,
  `last_achievement_date` INT UNSIGNED NOT NULL DEFAULT 0,  -- unix ts watermark
  `rewarded_quests`       JSON         NOT NULL,            -- baseline quest-id set
  `updated_at`            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`guid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Heroic Exploits: generated stories, one row per story per edition day.
-- story_key is `c<guid>` (individual) or `q<questid>` (a shared-quest group story),
-- so a quest finished by several characters the same day collapses to one row.
CREATE TABLE IF NOT EXISTS guildhall.exploit_news (
  `edition_date`  DATE         NOT NULL,
  `story_key`     VARCHAR(32)  NOT NULL,
  `subject`       VARCHAR(128) NOT NULL,   -- display label (name / "N adventurers")
  `headline`      VARCHAR(255) NOT NULL,
  `dek`           VARCHAR(512) NOT NULL,
  `content`       TEXT         NOT NULL,
  `author`        VARCHAR(128) NOT NULL,
  `author_title`  VARCHAR(128) NOT NULL,
  `dateline`      VARCHAR(64)  NOT NULL,
  `created_at`    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`edition_date`, `story_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Invite-link self-registration: outstanding/used invite tokens.
CREATE TABLE IF NOT EXISTS guildhall.invite (
  `id`                 INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `token_hash`         BINARY(32) NOT NULL,            -- sha256 of the URL token
  `inviter_account_id` INT UNSIGNED NOT NULL,
  `created_at`         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at`         TIMESTAMP NOT NULL,
  `used_at`            TIMESTAMP NULL DEFAULT NULL,
  `used_by_account_id` INT UNSIGNED NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_token` (`token_hash`),
  KEY `inviter_key` (`inviter_account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-account invite-token allowance override (absent = global default).
CREATE TABLE IF NOT EXISTS guildhall.invite_allowance (
  `account_id` INT UNSIGNED NOT NULL,
  `tokens`     INT NOT NULL,
  PRIMARY KEY (`account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Least-privilege application user
--   Change 'localhost' and the password to match your deployment.
-- ---------------------------------------------------------------------------
-- Set a real password here ONLY if you run this file to create the user. The
-- value you choose is what GUILDHALL_DB_PASSWORD must equal. To keep secrets out
-- of git, prefer creating/altering the user out-of-band, e.g.:
--   sudo mysql -e "ALTER USER 'guildhall'@'localhost' IDENTIFIED BY '<secret>';"
CREATE USER IF NOT EXISTS 'guildhall'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- The app fully owns its own database.
GRANT SELECT, INSERT, UPDATE, DELETE ON guildhall.* TO 'guildhall'@'localhost';

-- acore_auth: read identity columns, update only salt/verifier for password change.
GRANT SELECT (id, username, salt, verifier) ON acore_auth.account TO 'guildhall'@'localhost';
GRANT UPDATE (salt, verifier)               ON acore_auth.account TO 'guildhall'@'localhost';

-- acore_characters: read characters/skills/guild and held items (public game data).
GRANT SELECT ON acore_characters.characters        TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_skills  TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_spell   TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.guild             TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.guild_member      TO 'guildhall'@'localhost';
-- Auction House tab: held items (guid/flags to join inventory + detect soulbound).
GRANT SELECT (guid, owner_guid, itemEntry, count, flags, randomPropertyId) ON acore_characters.item_instance TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_inventory TO 'guildhall'@'localhost';
-- Heroic Exploits news: read earned achievements + completed quests.
GRANT SELECT ON acore_characters.character_achievement          TO 'guildhall'@'localhost';
GRANT SELECT ON acore_characters.character_queststatus_rewarded TO 'guildhall'@'localhost';

-- acore_world: live Auction House pricing reads item_template + npc_vendor for
-- the items a character holds (public game data, not sensitive).
CREATE USER IF NOT EXISTS 'ahpricing'@'172.25.%'
  IDENTIFIED BY 'password';
GRANT SELECT ON acore_world.item_template TO 'guildhall'@'172.25.%';
GRANT SELECT ON acore_world.npc_vendor    TO 'guildhall'@'172.25.%';
-- Heroic Exploits news: resolve quest names (achievement names come from the
-- client DBC via achievements.json -- acore_world.achievement_dbc is an empty stub).
GRANT SELECT ON acore_world.quest_template  TO 'guildhall'@'172.25.%';

-- Invite self-registration: column-scoped INSERT on account (create new accounts),
-- realmcharacters init, realmlist read, GM-level read to identify admins.
GRANT INSERT (username, salt, verifier, expansion, reg_mail, email) ON acore_auth.account TO 'guildhall'@'localhost';
GRANT SELECT, INSERT ON acore_auth.realmcharacters TO 'guildhall'@'localhost';
GRANT SELECT          ON acore_auth.realmlist       TO 'guildhall'@'localhost';
GRANT SELECT (id, gmlevel) ON acore_auth.account_access TO 'guildhall'@'localhost';

FLUSH PRIVILEGES;
