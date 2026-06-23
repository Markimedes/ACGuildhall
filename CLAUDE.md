# CLAUDE.md — Guildhall

Guidance for Claude Code working in **this** repository.

> **This is a standalone Python (Flask) web app**, not part of the C++ server.
> The AzerothCore `CLAUDE.md` in the parent directory describes the C++ emulator
> (CMake, worldserver, `data/sql/updates`, `-Werror`, conventional-commit scopes)
> and **does not apply here**. Ignore it for work inside `guildhall/`.

## What this is

A small player-facing web panel for an AzerothCore realm: self-service password
change (SRP6, direct DB), a guild-scoped forum, a profession roster, an auction
house front-end, an invite system, file downloads, and an AI news desk. It talks
**only to MySQL** as a least-privilege `guildhall` user — never to the worldserver.

See `README.md` for features and `REFACTOR_PLAN.md` for the design rationale and
the phase-by-phase history behind the structure below.

## Architecture (don't fight it)

- **Application factory + blueprints.** `create_app()` lives in
  `guildhall/__init__.py`. Route bodies live in per-area blueprints under
  `guildhall/` (`core`, `auth`, `forum`, `roster`, `auction`, `news`, `invites`,
  `downloads`, `admin`). New pages go in the matching blueprint, not the factory.
- **Two packages, one direction.** `guildhall/` (web) imports from `data/`
  (data-access + service layer). **`data/` must never import from `guildhall/`.**
  The dependency arrow is `guildhall → data`, always. Code a sidecar script
  (`generate_news.py`, `news_scheduler.py`) shares must live in `data/`.
- **Extensions in `extensions.py`.** CSRF, auth, and rate-limiting are
  Flask-WTF / Flask-Login / Flask-Limiter, instantiated unbound in
  `guildhall/extensions.py` and wired in the factory. **Do not hand-roll** CSRF
  tokens, session-based auth, or rate-limit bookkeeping — that plumbing was
  deliberately removed.
- **Config** is `config.py`: `Config` → `Development`/`Testing`/`ProductionConfig`,
  all `GUILDHALL_*` env reads. Read settings from `current_app.config`, never by
  re-reading env in a route.
- **Tooling** (`tools/`) is offline DBC parsers → committed JSON in `data/`.

## Hard rules

- **URLs are a contract.** Public links exist in the wild — especially
  `/invite/<token>`. Do not change existing paths or blueprint url-prefixes. If a
  template uses `url_for('auth.login')`, keep the endpoint name stable.
- **Data layer: raw SQL, multi-schema, NO ORM.** Queries span `acore_auth` /
  `acore_characters` / `acore_world` / `guildhall` and are schema-qualified +
  parameterized. Keep `mysql.connector`. Don't introduce SQLAlchemy.
  - Connection lifecycle: `data/db.py` scopes one pooled connection to the
    request via `g` + `teardown_appcontext`; outside an app context (sidecars) it
    borrows per-call. Use `_query`/`_query_one` for reads, `_execute` for writes
    that want **rowcount**, `_insert` for INSERTs that want **lastrowid**.
- **`data/srp6.py` is byte-exact with the core. Do not touch it.** It's verified
  against real account rows; a "cleanup" here silently breaks logins.
- **Security keepers** (relocated, not weakened — leave them working): the CSP +
  security headers in `guildhall/security.py`, the session-fixation
  `session.clear()` on login, the download path-traversal guard in
  `downloads`, parameterized SQL, non-root container.
- **Templates are flat** under `templates/`, shared by all blueprints, all
  extending `base.html`. Use Flask-Login's `current_user`
  (`current_user.is_authenticated` / `.username`) — **not** `session['account_id']`
  / `session['username']`, which are no longer set. (Using them broke the whole
  nav rail once; don't reintroduce that.)
- **Request format:** server-rendered HTML for document pages, JSON only for the
  interactive islands (the auction house). **We are not building an API/SPA.** The
  only sanctioned JSON seam is the still-deferred `api` blueprint described in
  `REFACTOR_PLAN.md` — discuss before expanding it.

## Dependencies

- Managed with **`uv`**; the locked set is `uv.lock` (committed, reproducible).
  **`requirements.txt` is gone — do not recreate it.**
- Install: `uv sync` (add `--extra dev` for pytest). The Docker image builds with
  `uv sync --frozen --no-dev`.
- Add a dep by editing `pyproject.toml` `dependencies`, then `uv lock`, then
  commit the updated `uv.lock`. Never edit `uv.lock` by hand.
- The app runs from source (`tool.uv.package = false`); it is not built as a wheel.

## Testing

- `uv run --extra dev pytest` (or `.venv/bin/python -m pytest`). Keep it green.
- Tests **fake `db` at the boundary** — `tests/conftest.py` stubs `db.init_pool`
  and individual tests monkeypatch the specific `db.*` they exercise. No live
  MySQL in unit tests.
- **Before changing existing behavior, characterization-test it first** (pin what
  it does today, then refactor). That's how this codebase has avoided regressions.
- A green `pytest` and a `200` response are **not** proof a page works — render
  output and real-DB writes aren't covered. Smoke real flows when it matters.

## Entry points

- `wsgi.py` → `app = create_app()` for gunicorn; `python wsgi.py` runs the dev
  server (host/port from `app.config`).
- `generate_news.py` / `news_scheduler.py` — the news-desk sidecar (its own
  docker-compose service); shares only `data/`, never the Flask app.

## Commits

Match this repo's own history. The refactor landed as `Refactor Phase N`; for new
work use a short imperative subject. Do **not** use the parent C++ repo's
`Type(Scope): ...` conventional-commit format.
