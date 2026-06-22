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

- [ ] Create `config.py` with a base `Config` reading every `GUILDHALL_*` var as
      class attributes with defaults, plus `DevelopmentConfig`, `TestingConfig`
      (`TESTING=True`, CSRF/limiter disabled or in-memory, no external services),
      and `ProductionConfig`.
- [ ] Move `SESSION_COOKIE_*`, `INVITE_*`, `NEW_ACCOUNT_EXPANSION`,
      `ADMIN_GMLEVEL`, `PUBLIC_BASE_URL`, `DEMAND_REFRESH_MINUTES`, `AH_*`,
      `DOWNLOADS_*`, `MAX_CONTENT_LENGTH` (NEW — cap the auction JSON payload)
      into it.
- [ ] **Kill the import-time config reads** in `soap.py`, `ahservice.py`,
      `exploits.py`. These currently freeze `URL`/`TIMEOUT`/limits at import,
      which is what breaks testability. Convert each module to read from
      `current_app.config` (or accept config via an `init_app`/init function
      called from the factory). This is the keystone change that makes the
      factory actually mean something.
- [ ] Factory does `app.config.from_object(config_class)`; keep the
      "required secret/DB creds" assertions, but raise from config validation.

Exit criteria: `create_app(TestingConfig)` and `create_app(ProductionConfig)`
both work; Phase 0 tests still green; no module reads `os.environ` at import.

---

## Phase 2 — Extensions (replace hand-rolled plumbing)

Introduce `extensions.py` and migrate the hand-rolled CSRF, auth, and rate
limiting to the standard libraries. Do these as **separate commits** so each is
reviewable and revertible.

- [ ] **`extensions.py`**: instantiate `csrf = CSRFProtect()`,
      `login_manager = LoginManager()`, `limiter = Limiter(...)`, and a place to
      hold the `NewsDesk` and DB pool handle. Factory calls `x.init_app(app)`.
      This removes the live objects currently stuffed into `app.config`
      (`_news_desk`, `_rate`).
- [ ] **Flask-WTF CSRF**: delete the hand-rolled `csrf_token()` /
      `before_request` CSRF in `security.py`. Flask-WTF provides
      `{{ csrf_token() }}` for templates automatically, so the templates that
      already emit `<input name="csrf_token">` keep working. Verify the wowhead
      external POST/connect paths aren't broken by CSRF (they're GET widgets, so
      fine).
- [ ] **Flask-WTF forms** (incremental): introduce `FlaskForm` subclasses for
      login, password change, forum post/reply, invite register, admin tokens.
      Move the scattered manual `request.form.get(...)` + length checks
      (`MAX_TITLE_LEN`, `MAX_BODY_LEN`, `USERNAME_RE`, `MAX_PASS_LEN`, …) into
      WTForms validators. Can be done per-blueprint during Phase 3 if it's too
      much in one pass.
- [ ] **Flask-Login**: add a `User` wrapper (`UserMixin`) and a
      `@login_manager.user_loader` that loads by `account_id`. Replace the custom
      `login_required`/`admin_required` decorators and raw `session["account_id"]`
      with `login_user`/`logout_user`/`@login_required`/`current_user`. Keep the
      `session.clear()` fixation guard. Move admin gating to a small
      `admin_required` that checks `current_user`'s gmlevel. **Rename** anything
      so it no longer shadows Flask-Login's `login_required`.
      Set `login_manager.session_protection = "basic"` (skill issue #5: "strong"
      logs out mobile/VPN users on IP change).
- [ ] **Flask-Limiter**: replace `_rate_limited` + the `app.config["_rate"]`
      dict. This fixes a real bug — the in-process dict is **per gunicorn
      worker**, so the documented "10/5min" login limit is actually ~30 across 3
      workers, and buckets never evict. Configure a shared storage backend
      (Redis if available; otherwise document the single-worker constraint).
      Re-apply the existing limits as decorators: login 10/5min, password
      5/5min, register 10/10min, ah-refresh 1 per `AH_REFRESH_SECONDS` per
      character (custom key func on char guid).

Exit criteria: CSRF, auth, and rate-limit behavior tests from Phase 0 still pass;
no mutable runtime state in `app.config`; rate limit is shared across workers (or
the limitation is explicitly documented).

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

Exit criteria: all Phase 0 tests green against blueprint URLs; manual smoke of
every nav link; existing invite links still resolve.

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
