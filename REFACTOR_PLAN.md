# Guildhall Standards Refactor Plan

Goal: bring the Guildhall Flask app in line with standard Flask structure
(application factory + Blueprints + extensions) and Python conventions, **without
regressing the security properties that are already correct** (parameterized
queries, CSP/security headers, session-fixation guard, SRP6 isolation, download
path-traversal guard, non-root gunicorn).

This is a structural refactor, not a rewrite. The SRP6 auth logic, the
multi-schema raw-SQL data layer, the news/exploit generation, and the SOAP/AH
service clients are all keepers — they get *relocated and rebound to the app*,
not replaced.

Each phase below is independently shippable and independently testable. Land them
in order; do not start a later phase until the earlier one is green.

---

## Target layout

```
guildhall/
├── pyproject.toml            # replaces requirements.txt (uv-managed, locked)
├── config.py                 # Config / DevelopmentConfig / TestingConfig / ProductionConfig
├── wsgi.py                   # gunicorn entrypoint: app = create_app()
│
├── guildhall/
│   ├── __init__.py           # create_app() factory only
│   ├── extensions.py         # csrf, login_manager, limiter, news_desk, db handle
│   ├── security.py           # CSP/header after_request, error handlers
│   ├── auth/                 # login, logout, password, character select
│   ├── forum/                # feeds, posts, replies, moderation
│   ├── roster/               # roster, player, demand
│   ├── auction/              # auctionhouse, review, list, refresh + build_ah_view
│   ├── news/                 # news, news_archive
│   ├── invites/              # invites, redeem, admin tokens
│   ├── downloads/            # downloads list + file handoff
│   └── core/                 # dashboard, shared context processor, jinja filters
│
├── data/                     # db.py, soap.py, ahservice.py, ahprices.py, srp6.py, etc.
│   (unchanged logic; only config sourcing changes)
│
├── templates/                # stays flat for now, or split per-blueprint in Phase 3
├── static/
└── tests/
    ├── conftest.py
    └── test_*.py
```

Note: keep the existing flat `templates/` working throughout. Per-blueprint
template folders are optional polish (Phase 3 tail), not a requirement.

---

## Phase 0 — Safety net (do this FIRST)  — IN PROGRESS

Refactoring untested code is how regressions happen. Establish a baseline before
moving anything.

- [x] Add `pyproject.toml` with **pinned** versions: `Flask>=3.1,<3.2` plus the
      matching `Werkzeug>=3.1` floor (replaces the loose `Flask>=3.0`), and a
      `dev` extra with `pytest`. NOTE: `uv` is not installed on this box, so no
      uv lockfile yet — used the existing venv's pip. Generating a uv lockfile
      and switching the Dockerfile off `requirements.txt` is deferred to the
      deploy cutover (Phase 4); `requirements.txt` stays valid until then. The
      Phase 2 extensions (flask-login / flask-wtf / flask-limiter) get added to
      `dependencies` when that phase lands.
- [x] Stand up `tests/conftest.py`: `app` fixture builds a real `create_app()`
      with required env set and `db.init_pool` stubbed to a no-op; `client` and
      `csrf_token` fixtures. (Built against the current hand-rolled config; moves
      to `TestingConfig` in Phase 1.)
- [x] Characterization tests landed (10 passing): pure helpers (`_to_gsc`,
      `_human_size`), download path-traversal rejection (`../`, absolute, nested,
      `.`/`..`, empty, missing), `login_required` redirect, CSRF rejection on
      POST, SRP6 login success/failure, login rate-limit 429.
- [x] REMAINING characterization coverage now landed: invite redeem happy path
      + bad-username/password-mismatch/"taken"; forum player-post success +
      too-long + empty rejection; `build_ah_view` classification
      (auctionable/soulbound/vendor-priced/not-priced + region + stack
      aggregation). 19 tests passing total.
- [x] DB-touching tests: settled on faking `db` at the boundary for unit tests
      (the data layer is already a thin function module). A small set of
      integration tests behind a marker can come later if needed.

Exit criteria: `pytest` green against the current, un-refactored app. **MET** —
19 tests passing against the current app. Phase 0 complete; ready for Phase 1.

---

## Phase 1 — Config classes

Replace the hand-rolled `load_config()` dict-builder and the import-time
`os.environ.get` module globals with standard Config classes.

- [x] Created `config.py` with `Config` (env read at *instantiation*, not import),
      plus `DevelopmentConfig`, `TestingConfig` (`TESTING=True`), `ProductionConfig`.
      `Config.validate()` does the fail-fast secret/DB-creds check. Service slices
      exposed as `DATABASE`/`SOAP`/`AHPRICING`/`EXPLOITS` dicts.
- [x] Moved `SESSION_COOKIE_*`, `INVITE_*`, `NEW_ACCOUNT_EXPANSION`,
      `ADMIN_GMLEVEL`, `PUBLIC_BASE_URL`, `DEMAND_REFRESH_MINUTES`, `AH_*`,
      `DOWNLOADS_*`, and `MAX_CONTENT_LENGTH` (NEW, 512 KB — caps the auction JSON
      payload) into `Config`.
- [x] **Killed the import-time config reads** in `soap.py`, `ahservice.py`,
      `exploits.py`. Chose the `configure(cfg)` injection pattern (like
      `db.init_pool`) over `current_app.config`, because the news-scheduler sidecar
      and the CLI tools use these modules with no app context. Static defaults at
      import → `configure()` overrides at startup. Wired into BOTH entry points:
      `create_app()` (soap+ahservice) and `generate_news.setup()` (ahservice+
      exploits); `tools/sample_prompts.py` calls them after loading its `.env`
      (`clear_news_cache.py` only uses the config-independent `exploits.is_obituary`,
      so it was left untouched).
- [x] Factory does `app.config.from_object(cfg)` (an instance); `cfg.validate()`
      raises on missing secret/DB creds. `__main__` now reads host/port from
      `app.config` instead of re-loading config (also clears a Phase 4 item).

Exit criteria: `create_app(TestingConfig)` and `create_app(ProductionConfig)`
both work; Phase 0 tests still green; no module reads `os.environ` at import.
**MET** — 19 tests green; all entry points import with no env/DB; production
`create_app` verified (config keys land in `app.config`, services configured,
`validate()` fails fast). Caveats: `news_ai.from_env(env=os.environ)` keeps a
default-arg *reference* to the live mapping (read lazily at call time, not frozen
— acceptable); `news_scheduler.py` is a standalone daemon entry script and still
reads its own `GUILDHALL_NEWS_*` at the top (not imported by the app; fold into
`Config` later if desired).

---

## Phase 2 — Extensions (replace hand-rolled plumbing)

Introduce `extensions.py` and migrate the hand-rolled CSRF, auth, and rate
limiting to the standard libraries. Do these as **separate commits** so each is
reviewable and revertible.

- [x] **`extensions.py`**: instantiates `csrf = CSRFProtect()`,
      `login_manager = LoginManager()`, `limiter = Limiter(key_func=
      get_remote_address, default_limits=[])`, plus the `User`/`user_loader`/
      `unauthorized_handler` and an `init_news_desk(app)`. Factory calls
      `x.init_app(app)`. The live objects left `app.config` (`_news_desk` →
      `app.extensions["news_desk"]`; `_rate` deleted). The DB pool handle stays
      in `db.py` for now (its lifecycle move to `g`/teardown is Phase 4).
- [x] **Flask-WTF CSRF**: deleted the hand-rolled `csrf_token()` /
      `before_request` CSRF. Flask-WTF registers the `{{ csrf_token() }}` Jinja
      global the templates already call, so no template changes. Set
      `WTF_CSRF_TIME_LIMIT = None` so tokens live as long as the session (the old
      token never expired; avoids 400ing a long-open login/redeem page — skill
      issue #6). Note: `session.clear()` on login rotates the token, which is
      correct — the next page renders a fresh one.
- [ ] **Flask-WTF forms** — DEFERRED to Phase 3. The routes are still one
      `_register_routes` closure with manual `request.form.get` + length checks;
      introducing `FlaskForm` subclasses is cleaner once each route lives in its
      blueprint (the plan already permits this). The manual validators are still
      pinned by the Phase 0 characterization tests, so the move stays safe.
- [x] **Flask-Login**: added `User(UserMixin)` (id = account id, `is_admin`
      resolved lazily/per-request) + `@login_manager.user_loader` by account id.
      Replaced the custom `login_required` (now Flask-Login's),
      `current_is_admin()` (now `current_user.is_admin`), and raw
      `session["account_id"]`/`session["username"]` with `login_user`/
      `logout_user`/`current_user`. Kept the `session.clear()` fixation guard
      (clear → `login_user`). `admin_required` now stacks `@login_required` then
      a gmlevel check. A custom `unauthorized_handler` preserves the exact old
      redirect (`/login?next=<path>`). `session_protection = "basic"` (skill
      issue #5).
- [x] **Flask-Limiter**: replaced `_rate_limited` + `app.config["_rate"]` with
      per-route `@limiter.limit` decorators: login 10/5min, password 5/5min
      (key `pw:{account}:{ip}`), register 10/10min, ah-refresh `1 per
      AH_REFRESH_SECONDS` per character (custom `key_func` on char guid, with
      `exempt_when` so no-character/SOAP-down requests don't burn the budget and
      `on_breach` to keep the friendly flash+redirect instead of a bare 429).
      Storage is `RATELIMIT_STORAGE_URI` (default `memory://`); the per-worker
      caveat + Redis pointer are documented in `config.py` and `.env.example`.
      No Redis on this box yet, so the multi-worker limitation is documented, not
      yet fixed (Redis at deploy).

Tests: the Phase 0 characterization tests were updated to the new mechanism
(Flask-WTF signed CSRF token in the `csrf_token` fixture; Flask-Login `_user_id`
session key + a `get_account_by_id` stub for the loader) — they assert the same
behavior, 19 still green. New runtime deps added to BOTH `pyproject.toml` and
`requirements.txt` (the Docker image still installs from the latter until Phase 4).

Exit criteria: CSRF, auth, and rate-limit behavior tests from Phase 0 still pass;
no mutable runtime state in `app.config`; rate limit is shared across workers (or
the limitation is explicitly documented). **MET** — 19 tests green; `_news_desk`
and `_rate` gone from `app.config`; the per-worker limit caveat is documented
with a Redis path. `create_app(TestingConfig)`/`(ProductionConfig)` both build
and the news sidecar still imports.

---

## Phase 3 — Blueprints

Split the single `_register_routes(app)` closure into Blueprints. Move route
bodies verbatim where possible; the earlier phases already removed the closure's
dependencies on factory-local helpers.

- [ ] Create blueprints: `core` (dashboard, context processor, jinja filters),
      `auth`, `password` (or fold into auth), `forum`, `roster`, `auction`,
      `news`, `invites`, `downloads`, `admin`.
- [ ] Move the factory-local helpers out of the closure to module scope in their
      blueprint: `_demand_for`/`_player_demand` → roster; `build_ah_view` and the
      `_sellable_by_guid`/`_posted_guids`/`_to_gsc`/`_flash_*` helpers → auction;
      `todays_news`/`_exploit_columns`/`_edition_neighbors` → news;
      `current_guild`/`active_character`/`current_characters` → shared (core or a
      small `context.py`).
- [ ] Update **every** `url_for("login")` → `url_for("auth.login")` etc. in
      routes and templates. Grep the templates — `base.html` references
      `dashboard`, `news`, `forum`, `roster`, `chronicle`, `auctionhouse`,
      `downloads`, `invites`, `admin_tokens`, `password`, `select_character`,
      `logout`. The `request.endpoint ==` active-nav checks also need the new
      `blueprint.endpoint` names.
- [ ] Register blueprints in the factory with appropriate `url_prefix`
      (`/forum`, `/roster`, `/auctionhouse`, `/admin`, …). Keep public routes
      (login, invite redeem) unprefixed where the current URLs must be preserved
      — invite links already in the wild must not break.

### Directory & module restructuring (do in this SAME pass)

Phase 3 already moves files into a package and rewrites imports, so do ALL the
file relocations here — doing them piecemeal earlier means a second round of
import/path edits. Settled layout: a `guildhall/` package for app+blueprints, a
`data/` area for the data-access/service modules, `tools/` for offline scripts.

Runtime-vs-tooling map (traced 2026-06-22 — drives what moves where):

- **Runtime (app):** `app.py`, `db.py`, `srp6.py`, `soap.py`, `ahprices.py`,
  `ahservice.py`, `professions.py`, `recipes.py`, `news_ai.py`, `news_prompts.py`.
- **Runtime (news scheduler sidecar):** `news_scheduler.py` → `generate_news.py`
  → `exploits.py` → `achievements.py`, `chartypes.py`, `weapons.py`.
  (`chartypes.py`/`weapons.py` look like stray data modules but ARE runtime deps.)
- **Offline tooling only (NOT imported at runtime):** `build_recipes.py`,
  `build_item_icons.py`, `build_achievements.py`. The app reads their committed
  JSON output, never the scripts. The latter two `import build_recipes`, so the
  three move together. Already-tooling: `tools/clear_news_cache.py`,
  `tools/sample_prompts.py`.

Path gotchas to handle during the moves (these are why a plain `git mv` breaks):

- [ ] **JSON data loads via `Path(__file__).with_name(...)`** — `item_icons.json`
      (ahprices), `recipes.json`+`vendor_items.json` (recipes), `achievements.json`
      (achievements). Each JSON must move ALONGSIDE its loader module, or the load
      path breaks.
- [ ] **Build scripts use `__file__`-relative input AND output**: `--out` defaults
      to `Path(__file__).with_name("X.json")` (writes next to the script) and
      `discover_dbc_dir()` anchors on `__file__.parent.parent`
      (→ `azerothcore/env/dist/data/dbc`). Moving them into `tools/` requires
      re-anchoring both to the repo root explicitly, so regenerated JSON lands
      where the runtime modules read it and DBC discovery still resolves.
      NOTE: regeneration can't be verified without the client DBCs present — fix
      the paths, then do one regen pass next time you're near the client data.
- [ ] **Tests follow the layout**: `tests/conftest.py` currently does `import app`
      with `pythonpath = ["."]`; update imports/pythonpath when `app` becomes a
      package and modules move under `data/`.

Exit criteria: all Phase 0 tests green against blueprint URLs; manual smoke of
every nav link; existing invite links still resolve; build scripts live in
`tools/` with repo-root-anchored paths (regen pass deferred until DBCs available).

---

## Phase 4 — Polish

- [ ] **Custom error handlers** (`@app.errorhandler` for 400/403/404/429/500)
      rendering styled templates instead of Werkzeug defaults. Several routes
      `abort(404)`/`abort(403)` today and get an unstyled page.
- [ ] **Hoist function-local imports** (`current_app`, `datetime.date`,
      `itertools.groupby`, etc.) to module top now that the closure is gone and
      the cycles are broken.
- [ ] **DB connection lifecycle**: bind the pool to the app and scope a
      connection to the request via `g` + `teardown_appcontext`, instead of the
      module global `_pool` + per-query get/close. Tighten `_execute`'s ambiguous
      `lastrowid or rowcount` return (split into `insert()` vs `execute()`).
      Keep raw SQL + multi-schema — do **not** introduce an ORM.
- [ ] **`__main__` cleanup**: dev entrypoint should not call `load_config()`
      twice; read host/port from `app.config`. Move gunicorn target to `wsgi.py`.
- [ ] Optional: per-blueprint `templates/` folders.

Exit criteria: full test suite green; styled error pages; no import-inside-
function except where a genuine cycle requires it.

---

## Request-format principle (applies now and to all future work)

Settled convention — the app stays **server-rendered HTML for document-shaped
pages** (news, roster, forum, chronicle, downloads, player profiles) and uses
**JSON for the genuinely interactive islands** (the auction house already does
this). We are NOT converting to an API + SPA: single maintainer, the
SameSite=Lax session-cookie auth model is already correct, and there's no second
client to justify the decoupling (YAGNI).

Rules:

- **Structured/nested data → JSON body, not flattened form fields.** Lists and
  objects do not survive form-urlencoding cleanly (CSV-in-a-field, repeated
  keys), which is exactly why the owner moved the auction-list payload to JSON.
  Reserve HTML form fields for flat scalar inputs (login, password, single-field
  forms). Validate JSON bodies server-side.
- **Method semantics stay correct regardless of format:** reads = GET (cacheable,
  bookmarkable, safe to retry), writes = POST/PUT/PATCH/DELETE. Do not collapse
  reads into POST just because JSON is being used.
- **CSRF:** primary controls remain SameSite=Lax cookies + CSRF tokens
  (Flask-WTF after Phase 2). Enforcing `application/json` content-type is
  defense-in-depth only, and only counts if the handler actually *rejects*
  non-JSON.

### `api` blueprint (Phase 3 deliverable, defining the seam)

Give the interactive JSON endpoints a bounded home so they're consistent and
later promotable to a public API without a rewrite:

- [ ] An `api` blueprint (`url_prefix="/api"`) holding the JSON endpoints
      (auction review/list to start). Keep cookie-session auth — no new token
      scheme.
- [ ] A single JSON error shape, e.g. `{"error": {"code": str, "message": str}}`,
      returned by `api`-scoped error handlers, so clients get structured failures
      instead of HTML error pages.
- [ ] Content negotiation where a route serves both a browser and a JSON client
      (`request.is_json` / `Accept`), so the HTML path and the JSON path share
      one handler rather than diverging.
- [ ] Enforce JSON content-type on `api` writes and reject otherwise (the
      defense-in-depth note above) — but still require the CSRF token.

This is the trigger point to harden into a versioned public API *if* a real
second consumer (mobile client, Discord bot) ever appears — extracted from a
clean seam, not guessed at speculatively today.

## Explicitly out of scope (keep as-is)

- SRP6 implementation (`srp6.py`) — verified byte-exact, leave it.
- Raw-SQL data layer across `acore_auth`/`acore_characters`/`acore_world` —
  multi-schema reads make Flask-SQLAlchemy a poor fit; keep `mysql.connector`.
  Only the *connection lifecycle* changes (Phase 4), not the queries.
- News/exploit generation logic (`news_ai.py`, `news_prompts.py`,
  `exploits.py`, `generate_news.py`) and the scheduler sidecar — only their
  config sourcing changes (Phase 1).
- CSP / security-header policy — already correct; just relocates to
  `security.py`.
- The download X-Accel-Redirect / path-traversal guard — correct; relocates to
  the downloads blueprint unchanged.

---

## Risk notes

- **Invite links in the wild**: the `/invite/<token>` URL must not change.
  Preserve it (unprefixed blueprint route).
- **CSRF cutover**: hand-rolled and Flask-WTF both look for a `csrf_token` form
  field, so templates don't need changing — but verify the token name/format
  match during the Phase 2 swap, and that an in-flight session mid-deploy
  doesn't 400 every POST (skill issue #6: align cache headers; our POST targets
  shouldn't be cached anyway).
- **Rate-limit backend**: if no Redis, Flask-Limiter falls back to in-memory and
  the multi-worker problem persists — call that out at deploy time rather than
  silently shipping the same bug under a new name.
- **Flask-Login session protection**: use `"basic"`, not `"strong"` (IP-change
  logouts).
```
