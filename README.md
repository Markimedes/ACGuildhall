# Guildhall

A small player-facing web panel for an AzerothCore realm. Players log in with
their game account and can:

- **Change their own password** (written straight to `acore_auth` using AzerothCore's
  SRP6 scheme — no SOAP, no GM credential, works even when the worldserver is offline).
- **Use a guild-scoped mini-forum** — an **Announcements** feed only the guild leader
  can post to, and an open **Member Board** any guild member can post to, with replies
  on every post.
- **View the guild profession roster** — who can craft what, read-only from
  `character_skills`. Each profession shows a crowned **&lt;Profession&gt; Master**
  (the guild's highest-skill member), and every member links to a player page listing
  their **known recipes** grouped by profession.
- **Download shared files** — a `/downloads` page lists the files in a configured
  directory (e.g. the game client and addon packs). Behind nginx, large files are
  streamed by nginx itself via `X-Accel-Redirect` (with pause/resume), so Flask only
  authenticates the request. See [DEPLOY.md](DEPLOY.md#4b-shared-downloads-optional).
- **Invite players** — each account has a pool of invite tokens; spending one mints a
  unique link valid 12h. A new player redeems the link to **create their own account**
  (SRP6, direct DB insert, mirroring `AccountMgr::CreateAccount`). Unused links auto-refund
  the token (lazy accounting — no background job). Allowance is a config default with an
  optional per-account override in `guildhall.invite_allowance`.

It talks **only to MySQL** (never to the worldserver) as a dedicated least-privilege
`guildhall` user. See [plan.md](plan.md) for the full design rationale.

## Layout

| Path | Purpose |
|------|---------|
| `wsgi.py` | Gunicorn/dev entrypoint: `app = create_app()` |
| `config.py` | `Config`/`Testing`/`Production` classes (all `GUILDHALL_*` env reads) |
| `guildhall/` | Flask app package: `create_app` factory, `extensions.py`, `security.py`, and per-area blueprints (`auth`, `forum`, `roster`, `auction`, `news`, `invites`, `downloads`, `admin`, `core`) |
| `data/` | Data-access + service layer: `db.py`, `srp6.py`, `soap.py`, `ahservice.py`, `ahprices.py`, `professions.py`, `recipes.py`, `news_ai.py`, … plus their committed JSON (`recipes.json`, `item_icons.json`, `achievements.json`, …) |
| `tools/` | Offline tooling: `build_recipes.py`/`build_item_icons.py`/`build_achievements.py` (DBC parsers → the committed JSON) and maintenance scripts |
| `generate_news.py`, `news_scheduler.py` | News-desk sidecar entrypoints (the docker-compose `news-scheduler` service) |
| `schema.sql` | Custom forum tables + `guildhall` MySQL user/grants |
| `templates/`, `static/` | Jinja2 templates and CSS (flat, shared by all blueprints) |

## Setup

1. **Create a virtualenv and install deps**

   ```bash
   cd guildhall
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Apply the schema and create the DB user.** Edit `schema.sql` first to set the
   `guildhall` user's host and password, then:

   ```bash
   mysql -u root -p < schema.sql
   ```

3. **Configure.** All config comes from `GUILDHALL_*` environment variables:

   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_hex(32))"   # GUILDHALL_SECRET_KEY
   # edit .env: paste the secret key, set the DB password to match schema.sql
   ```

   docker-compose loads `.env` automatically. To run `python wsgi.py` directly,
   export those variables into your shell first (e.g. `set -a; . ./.env; set +a`).

4. **Build the recipe reference** (once; re-run only if the client DBCs change).
   Recipe names/profession mappings live in the client DBC files, not MySQL:

   ```bash
   python tools/build_recipes.py        # auto-finds env/dist/data/dbc, writes data/recipes.json
   # or: python tools/build_recipes.py --dbc-dir /path/to/dbc
   ```

   It also reads `item_template` (default `acore`/`acore` — override with
   `--db-user/--db-pass/--db-host/--db-name`) to bake reagent/product **item names**
   into the bundle, so the running app needs no `acore_world` access. The bundle
   carries each recipe's difficulty thresholds and reagents; `recipes.json` is
   committed, so this is only needed to regenerate it.

   Likewise build the **achievement reference** for the news desk's Heroic Exploits
   (achievement names live in `Achievement.dbc`, not MySQL):

   ```bash
   python tools/build_achievements.py   # auto-finds env/dist/data/dbc, writes data/achievements.json
   ```

   `achievements.json` is committed too; re-run only if the client DBCs change.

5. **Run.**

   ```bash
   python wsgi.py        # serves on 127.0.0.1:5000 by default
   ```

   For anything beyond local testing, run it behind a reverse proxy that terminates
   TLS (nginx/Caddy) and set `GUILDHALL_BEHIND_TLS=true`.

## Docker deployment

For running as a container behind nginx (gunicorn, env-var config, joins an existing
Docker network), see [DEPLOY.md](DEPLOY.md). Configuration comes entirely from
`GUILDHALL_*` environment variables (see `.env.example`),
and the app trusts one proxy hop (`GUILDHALL_TRUST_PROXY=true`) for correct client IPs
and Secure cookies behind nginx.

## Security model (summary)

- The logged-in account id comes **only** from the server-side session. The password
  form has no account/username field, so a session can only ever change its own
  password.
- Forum identity (`author_guid`, `guildid`, leader status) is recomputed from the
  session on every request; submitted post/reply ids are re-checked to belong to the
  viewer's guild (blocks cross-guild access).
- Announcements (official feed) are restricted to the guild leader server-side, not
  just by hiding the form.
- All user text is stored raw and HTML-escaped on output (Jinja autoescape; the
  `nl2br` filter escapes *before* inserting `<br>`). A strict Content-Security-Policy
  with no inline scripts is sent on every response. CSRF tokens on all forms.

## Verifying the SRP6 port

Before trusting password changes, confirm the port matches the core against a real
account row (replace the literals):

```python
import srp6
# salt/verifier are the binary(32) columns from acore_auth.account
assert srp6.check_login("KNOWNUSER", "knownpass", salt_bytes, verifier_bytes)
```

A freshly created account (`.account create paneltest paneltest` in the worldserver
console) should satisfy `check_login("PANELTEST", "PANELTEST", salt, verifier)`.
