-- Migration: move Guildhall's tables out of the acore_* schemas into their own
-- `guildhall` database. Run ONCE on an existing install. Uses RENAME TABLE, which
-- moves the tables (with their data) across databases on the same MySQL instance
-- in a single atomic step. Run as a user that can CREATE DATABASE and move these
-- tables (e.g. root):
--     mysql -u root -p < migrations/001_move_to_guildhall_db.sql
--
-- Fresh installs do NOT need this -- schema.sql already creates everything in the
-- `guildhall` database.

CREATE DATABASE IF NOT EXISTS guildhall
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Move the five legacy tables that hold live data. guild_post and its reply child
-- move together in one statement so the foreign key is updated atomically. Drop
-- the redundant `guildhall_` prefix on the way (the database name namespaces them).
RENAME TABLE
  acore_characters.guild_post              TO guildhall.guild_post,
  acore_characters.guild_post_reply        TO guildhall.guild_post_reply,
  acore_characters.guildhall_player_demand TO guildhall.player_demand,
  acore_auth.guildhall_invite              TO guildhall.invite,
  acore_auth.guildhall_invite_allowance    TO guildhall.invite_allowance;

-- The news cache (guildhall.news) is new -- there is nothing to move, so just
-- create it fresh. If you already created guildhall_news in acore_characters,
-- add it to the RENAME above instead and delete this CREATE.
CREATE TABLE IF NOT EXISTS guildhall.news (
  `category`     VARCHAR(32)  NOT NULL,
  `event_date`   DATE         NOT NULL,
  `headline`     VARCHAR(255) NOT NULL,
  `dek`          VARCHAR(512) NOT NULL,
  `content`      TEXT         NOT NULL,
  `author`       VARCHAR(128) NOT NULL,
  `author_title` VARCHAR(128) NOT NULL,
  `dateline`     VARCHAR(64)  NOT NULL,
  `created_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`category`, `event_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Grant the app access to its new database (match the host to your deployment).
GRANT SELECT, INSERT, UPDATE, DELETE ON guildhall.* TO 'guildhall'@'localhost';

-- The old per-table grants on acore_* now point at tables that no longer exist
-- there; they are harmless, but you may revoke them for tidiness, e.g.:
--   REVOKE ALL PRIVILEGES ON acore_characters.guild_post FROM 'guildhall'@'localhost';
--   REVOKE ALL PRIVILEGES ON acore_auth.guildhall_invite  FROM 'guildhall'@'localhost';

FLUSH PRIVILEGES;
