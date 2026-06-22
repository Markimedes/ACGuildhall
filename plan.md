# Guildhall — player web panel (password change, guild announcements, guild skills)

> Lives in a new top-level repo directory **`guildhall/`**. The plan itself is saved
> as `guildhall/plan.md`.

## Context

The server has no player-facing self-service tooling. Players currently can only
change passwords via a GM (`.account set password` in [cs_account.cpp](src/server/scripts/Commands/cs_account.cpp))
and there is nowhere to read guild news or see who can craft what. We want a small,
read-mostly web panel where a player can:

1. **Change their own account password** (the one write that touches `acore_auth`).
2. **Read & participate in a guild-scoped mini-forum** — two feeds:
   - an **official feed** only officers can post to, and
   - a **player feed** any guild member can post to,
   with **threaded replies** allowed on every post in both feeds.
3. **View the guild profession roster** ("guild skills") — read-only, derived from
   `character_skills`.

Stack: **Python**, in a new top-level `guildhall/` directory (a peer of `src/`,
modeled on the existing [tools/quest_builder/](tools/quest_builder/) Python tooling).
Recommended framework: **Flask + Jinja2** (server-rendered pages, cookie session,
no SPA/build step) — the right weight for a 3-page player panel. FastAPI would only
pay off if you later want a JSON API + separate frontend; not needed here.

## Communication model

Guildhall talks **only to MySQL** — it never connects to the worldserver and does **not**
use SOAP/console. Rationale: the forum and roster need arbitrary queries and custom-table
writes, which SOAP cannot do, so the whole app shares the server's databases directly. The
password change also goes direct-to-DB via the SRP6 port (no SOAP, no stored GM credential,
works even if the worldserver is offline). The worldserver picks up a changed
`salt`/`verifier` on the account's next login automatically.

Access is via a **dedicated least-privilege MySQL user** (`guildhall`), not the
worldserver's credentials. Required grants only:
- `acore_auth`: `SELECT (id, username, salt, verifier)` on `account`; `UPDATE (salt, verifier)` on `account`.
- `acore_characters`: `SELECT` on `characters`, `character_skills`, `guild` (for
  `leaderguid`), `guild_member`; `INSERT`/`SELECT`/`DELETE` on the custom `guild_post`,
  `guild_post_reply`.

## Key facts verified in this tree

- **SRP6 password** ([SRP6.cpp:39](src/common/Cryptography/Authentication/SRP6.cpp#L39),
  [AccountMgr.cpp:217-246](src/server/game/Accounts/AccountMgr.cpp#L217)):
  `acore_auth.account` has `salt binary(32)`, `verifier binary(32)` — **no password hash**.
  Username **and** password are upper-cased (`Utf8ToUpperOnlyLatin`) before hashing.
  - `g = 7`, `N = 0x894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7`
  - `x = SHA1( salt ‖ SHA1(UPPER(user) ":" UPPER(pass)) )`, interpreted **little-endian**
  - `verifier = pow(g, x, N)`, stored as **little-endian** 32 bytes; `salt` is 32 raw random bytes
  - Password update SQL (mirrors `LOGIN_UPD_LOGON`):
    `UPDATE account SET salt=%s, verifier=%s WHERE id=%s`
- **Profession roster**: `character_skills(guid, skill, value, max)`
  ([character_skills.sql](data/sql/base/db_characters/character_skills.sql)).
  Skill→name mapping is a **static dict** in the app (names live in SkillLine.dbc, not MySQL):
  164 Blacksmithing, 165 Leatherworking, 171 Alchemy, 182 Herbalism, 186 Mining,
  197 Tailoring, 202 Engineering, 333 Enchanting, 393 Skinning, 755 Jewelcrafting,
  773 Inscription, 129 First Aid, 185 Cooking, 356 Fishing.
- **Account → roster join**: `account.id → characters.account → characters.guid → character_skills.guid`,
  and `characters.guid → guild_member.guid → guild_member.guildid`.
- **Officer gating** for posting announcements: `guild_member.rank → guild_rank(guildid, rid).rights`;
  treat rank 0 (GM) or `rights & 0x1000` (`GR_RIGHT_SETMOTD`) as "officer".
- **Announcements need a new table** (nothing stock fits) — added to `acore_characters`
  since all guild data lives there.

## Deliverable layout — `guildhall/` (new top-level dir)

```
guildhall/
  plan.md                # this plan
  app.py                 # Flask app factory, routes
  srp6.py                # MakeRegistrationData / CheckLogin (pure-python port of SRP6.cpp)
  db.py                  # mysql.connector pools for acore_auth + acore_characters
  professions.py         # static skill-id → name map
  config.example.toml    # DB creds, secret key, host/port  (real config gitignored)
  schema.sql             # CREATE TABLE guild_announcement (applied to acore_characters)
  requirements.txt       # Flask, mysql-connector-python, tomli
  templates/             # base.html, login.html, dashboard.html, password.html,
                         #   forum_feed.html, forum_post.html, roster.html
  static/style.css
  README.md
```

## Implementation steps

### 1. SRP6 module (`srp6.py`)
Pure-Python port, the trust-critical piece:
- `N = 0x894B...3E9BB7`, `g = 7`.
- `calculate_verifier(user, pwd, salt)`:
  `h1 = sha1((user.upper()+":"+pwd.upper()).encode()).digest()`;
  `x = int.from_bytes(sha1(salt + h1).digest(), "little")`;
  return `pow(g, x, N).to_bytes(32, "little")`.
- `make_registration_data(user, pwd)` → `(os.urandom(32), verifier)`.
- `check_login(user, pwd, salt, verifier)` → recompute verifier from stored salt, compare.
- Unit-test against a known account row to confirm endianness before wiring anything else.

### 2. DB layer (`db.py`)
Two connection pools (`acore_auth`, `acore_characters`) using `mysql-connector-python`,
parameterized queries only — connecting as the dedicated least-privilege `guildhall` user
(creds from config.toml). Writes are limited to: the password `UPDATE` on `account`, and
`INSERT`/`DELETE` on `guild_post` / `guild_post_reply`. Ship the `CREATE USER` + `GRANT`
statements (matching the Communication model grants) in `schema.sql` so setup is one step.

### 3. Auth / session
- `GET/POST /login`: look up `account.id, username, salt, verifier` by username;
  `srp6.check_login(...)`; on success store `account_id` + `username` in a signed
  Flask session cookie. Generic "invalid credentials" on failure; basic rate-limit
  by IP (in-memory counter is fine for a private realm).
- `@login_required` decorator guards every other route. `/logout` clears session.

### 4. Password change — `GET/POST /password`
Require current password (re-`check_login`), new password twice, enforce
`len ≤ 16` (`MAX_PASS_STR`). On success: `make_registration_data(username, new)` →
`UPDATE account SET salt=%s, verifier=%s WHERE id=%s`. Flash success.

### 5. Guild mini-forum (official + player feeds, with replies)
- `schema.sql` — two tables in `acore_characters`:
  ```sql
  CREATE TABLE IF NOT EXISTS guild_post (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    guildid     INT UNSIGNED NOT NULL,
    feed        ENUM('official','player') NOT NULL,
    author_guid INT UNSIGNED NOT NULL,
    title       VARCHAR(128) NOT NULL,
    body        VARCHAR(2000) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id), KEY guild_feed_key (guildid, feed, created_at)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

  CREATE TABLE IF NOT EXISTS guild_post_reply (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    post_id     INT UNSIGNED NOT NULL,
    author_guid INT UNSIGNED NOT NULL,
    body        VARCHAR(2000) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id), KEY post_key (post_id, created_at),
    CONSTRAINT fk_reply_post FOREIGN KEY (post_id)
      REFERENCES guild_post(id) ON DELETE CASCADE
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
  ```
- **Resolve the account's guild & role**: pick the highest-level character of theirs that
  has a `guild_member` row → `guildid`. `is_leader` = the account owns the character whose
  `guid == guild.leaderguid` for that `guildid`. If no guild, the forum routes show a
  friendly "join a guild to participate" state; password + roster still work.
- **Permission matrix** (enforced server-side on every POST, derived from the session
  identity — never from form fields):
  | Action | Who |
  |---|---|
  | Post to `official` feed (announcements) | **guild leader only** (`is_leader`) |
  | Post to `player` feed | any guild member |
  | Reply to any post | any guild member |
  | Delete a post/reply | its author, or the guild leader (moderation) |
- **Routes**:
  - `GET /forum` → tabs/links to both feeds; counts per feed.
  - `GET /forum/<feed>` (`official`|`player`) → posts for `(guildid, feed)` newest-first,
    joined to `characters.name` for author + reply counts. "New post" form on the
    `official` feed shown only when `is_leader`.
  - `POST /forum/<feed>` → if `feed=='official'` require `is_leader` (else 403); escape
    HTML; `INSERT` with `author_guid`/`guildid` taken from the session identity, not the form.
  - `GET /forum/post/<id>` → single post + its replies (verify the post's `guildid`
    matches the viewer's guild), plus a reply form for any member.
  - `POST /forum/post/<id>/reply` → `INSERT` into `guild_post_reply`.
  - `POST /forum/post/<id>/delete` / `POST /forum/reply/<id>/delete` → allow if the
    session identity is the author or `is_leader`; CASCADE removes a post's replies.
- All bodies stored as **raw plain text** (no HTML/markup/BBCode — keeps it genuinely
  "limited" and means the DB never holds markup). Rendering is escape-then-format; see the
  XSS subsection in step 7. CSRF token on every form.

### 6. Guild profession roster ("guild skills") — `GET /roster`
Reuse the same `(guildid, is_leader)` resolver from step 5.
For the account's `guildid`, list guild members with their professions:
```sql
SELECT c.name, c.level, cs.skill, cs.value, cs.max
FROM guild_member gm
JOIN characters c        ON c.guid = gm.guid
JOIN character_skills cs ON cs.guid = c.guid
WHERE gm.guildid = %s AND cs.skill IN (<profession ids>)
ORDER BY c.name;
```
Map `cs.skill` → name via `professions.py`; render grouped by character (or pivot to
a "who can craft X" table). Read-only.

### 7. Security model (identity & authorization)
The core invariant: **every privileged action derives its subject from the server-side
session, never from client input.**
- On login, the signed session cookie stores `account_id` (+ `username`) — set only after
  `srp6.check_login` succeeds. `@login_required` guards all non-login routes.
- **Password change is self-only by construction:** the password form has *no* username or
  account-id field. The route ignores any such input and runs
  `UPDATE account SET salt=?, verifier=? WHERE id = <session.account_id>`. There is no code
  path that lets one session set another account's password.
- **Forum identity:** `author_guid`, `guildid`, and `is_leader` are recomputed from
  `session.account_id` on every request (resolver in step 5). Form fields never carry
  identity. A submitted `post_id`/`reply_id` is always re-checked to belong to the viewer's
  `guildid` before read or write (blocks cross-guild IDOR).
- **Announcements (official feed) are leader-only**, enforced server-side on `POST`
  (`is_leader` required), not just by hiding the form.
- Session cookies: `HttpOnly`, `SameSite=Lax`, `Secure` when served over TLS; rotate the
  session id on login. CSRF token on all POST forms. Parameterized SQL everywhere.

**XSS prevention** (the forum displays player-controlled text — post titles/bodies, reply
bodies, character names — so stored XSS is the main risk):
1. **Output encoding is the primary defense.** Jinja2 autoescape is on for all `.html`
   templates; every user value (`{{ post.title }}`, `{{ reply.body }}`, `{{ char.name }}`)
   is HTML-escaped in HTML and quoted-attribute contexts. **No `|safe` / `Markup()` on any
   user-derived value, ever.**
2. **Safe newline handling.** The "newlines→`<br>`" nicety is done *after* escaping, never
   before. Implement one Jinja filter, e.g.
   `nl2br = lambda s: Markup('<br>').join(escape(s).split('\n'))` — it escapes each segment
   and only the literal `<br>` separators are trusted. Never `escape()` after inserting `<br>`.
3. **No user data in dangerous sinks.** No inline `<script>`, no `onclick=`/event handlers,
   no building `href`/`src`/`style`/`<script>` content from user input. Static JS/CSS only,
   served from `static/`.
4. **Content-Security-Policy as defense-in-depth** (set on every response):
   `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none';
   base-uri 'none'; frame-ancestors 'none'`. With no inline scripts, this neutralizes most
   injection even if an escaping bug slips through. Plus `X-Content-Type-Options: nosniff`
   and `Referrer-Policy: same-origin`.
5. **Input caps (hardening, not the XSS defense):** enforce title ≤128 / body ≤2000 and
   strip control chars on write; encoding above is what actually prevents XSS.

### 8. Config & hardening
- `config.example.toml` with the `guildhall` DB creds, `secret_key`, bind host/port; real
  config gitignored. Run behind the user's existing reverse proxy / TLS — the app speaks
  plain HTTP locally. Per-IP rate-limit on `/login` and on password change.

## Verification

1. `pip install -r requirements.txt`, copy `config.example.toml` → `config.toml`, fill creds.
2. **SRP6 correctness (do first):** in a REPL, `check_login()` an existing account with
   its real password against the stored `salt`/`verifier` → must return True. Then change a
   throwaway test account's password via the panel and confirm in-game login works.
   Cross-check: `.account create paneltest paneltest` in worldserver console, then verify
   `check_login("PANELTEST","PANELTEST", salt, verifier)` is True.
3. Apply `schema.sql` (tables + `guildhall` user/grants) to the databases. Forum checks:
   - As the **guild leader's** account: post to both `official` and `player` feeds; reply.
   - As a **non-leader** member: confirm the `official` "new post" form is hidden and a
     direct `POST /forum/official` returns 403; confirm posting to `player` and replying work.
   - Confirm author and leader can delete; a third member cannot.
4. Roster: pick a guild with known professions, confirm names/levels match in-game.
5. Negative/authorization tests:
   - wrong current password rejected; over-long password rejected.
   - **Self-only password:** a forged request with another account's id/username still only
     changes the logged-in account (id comes from the session); verify the other account's
     `salt`/`verifier` are unchanged.
   - cross-guild IDOR blocked (viewing/replying to a `post_id` from another guild → 403/404).
   - **Stored XSS:** submit a post body of `<script>alert(1)</script>` and a body with
     `<img src=x onerror=alert(1)>`; confirm both render as inert visible text (escaped) for
     a second viewer, no alert fires, and the response carries the CSP header.

## Notes / decisions left open
- **Framework**: plan uses Flask; say the word if you'd rather FastAPI.
- **Forum tables location**: `guild_post` / `guild_post_reply` live in `acore_characters`,
  shipped as the tool's own `schema.sql` (not core `data/sql/updates/pending_*`, since
  they're private panel tables, not a core PR). Easy to move if you'd prefer AC's updater.
- **Forum scope**: deliberately flat — two feeds, one level of replies, plain-text bodies,
  no edit history / pagination / reactions. Pagination can be added later if a feed grows.
- **Self-registration is out of scope** (you asked for password change only). The SRP6
  module already supports it if you want to add a `/register` route later.
