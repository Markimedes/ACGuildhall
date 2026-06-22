"""Guildhall -- player web panel for an AzerothCore realm.

Features: self-service password change (SRP6, direct to acore_auth), a
guild-scoped mini-forum (official feed = guild leader only, player feed = any
member, with replies), and a read-only guild profession roster.

Run:  python app.py   (configured entirely via GUILDHALL_* env vars; see
.env.example, which docker-compose loads)
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from urllib.parse import quote
from markupsafe import Markup, escape

import ahprices
import ahservice
import db
import news_prompts
import professions
import recipes
import soap
import srp6
from news_ai import NewsDesk
from professions import profession_name

# Auction listing: valid durations (hours) and the deposit-estimate inputs. The
# worldserver computes the authoritative deposit; these only drive the preview and
# must match the realm's AuctionHouse.dbc deposit (5 = faction AH) and
# Rate.Auction.Deposit. See soap.py for the SOAP command channel.
AUCTION_DURATIONS = (12, 24, 48)

MAX_PASS_LEN = 16  # mirrors AccountMgr MAX_PASS_STR
MAX_ACCOUNT_LEN = 17  # mirrors AccountMgr MAX_ACCOUNT_STR
MAX_EMAIL_LEN = 255  # mirrors AccountMgr MAX_EMAIL_STR
MAX_TITLE_LEN = 128
MAX_BODY_LEN = 2000
VALID_FEEDS = ("official", "player")
# Account names: letters/digits/_-. ; no ':' (SRP6 delimiter) or whitespace.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,17}$")


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _human_size(num: int) -> str:
    """Render a byte count as a short human-readable string (e.g. '24.5 GB')."""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def list_downloads(dirpath: str) -> list[dict]:
    """Regular, non-hidden files directly inside ``dirpath`` (no recursion),
    sorted by name. Returns dicts with name, size, human size and mtime."""
    base = Path(dirpath)
    out: list[dict] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_file():
                continue
            st = entry.stat()
        except OSError:
            continue
        out.append({
            "name": entry.name,
            "size": st.st_size,
            "size_human": _human_size(st.st_size),
            "modified": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
        })
    return out


def resolve_download(dirpath: str, name: str) -> Path | None:
    """Resolve ``name`` to a real file directly inside ``dirpath``, or None.

    Rejects any path separators / traversal: the requested name must be a bare
    filename whose resolved path stays inside the (resolved) downloads dir.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    base = Path(dirpath).resolve()
    try:
        target = (base / name).resolve()
        if target.parent != base or not target.is_file():
            return None
    except OSError:
        return None
    return target


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> dict:
    """Build the config from GUILDHALL_* environment variables.

    Everything comes from the environment (see .env.example, which docker-compose
    loads), so no secrets file is baked into or mounted onto the image.
    """
    cfg: dict = {}
    app_cfg = cfg.setdefault("app", {})
    db_cfg = cfg.setdefault("database", {})

    env = os.environ
    if "GUILDHALL_SECRET_KEY" in env:
        app_cfg["secret_key"] = env["GUILDHALL_SECRET_KEY"]
    if "GUILDHALL_HOST" in env:
        app_cfg["host"] = env["GUILDHALL_HOST"]
    if "GUILDHALL_PORT" in env:
        app_cfg["port"] = int(env["GUILDHALL_PORT"])
    app_cfg["behind_tls"] = _env_bool("GUILDHALL_BEHIND_TLS", app_cfg.get("behind_tls", False))
    app_cfg["trust_proxy"] = _env_bool("GUILDHALL_TRUST_PROXY", app_cfg.get("trust_proxy", False))
    if "GUILDHALL_INVITE_TOKENS_DEFAULT" in env:
        app_cfg["invite_tokens_default"] = int(env["GUILDHALL_INVITE_TOKENS_DEFAULT"])
    if "GUILDHALL_INVITE_TTL_HOURS" in env:
        app_cfg["invite_ttl_hours"] = int(env["GUILDHALL_INVITE_TTL_HOURS"])
    if "GUILDHALL_NEW_ACCOUNT_EXPANSION" in env:
        app_cfg["new_account_expansion"] = int(env["GUILDHALL_NEW_ACCOUNT_EXPANSION"])
    if "GUILDHALL_ADMIN_GMLEVEL" in env:
        app_cfg["admin_gmlevel"] = int(env["GUILDHALL_ADMIN_GMLEVEL"])
    if "GUILDHALL_PUBLIC_BASE_URL" in env:
        app_cfg["public_base_url"] = env["GUILDHALL_PUBLIC_BASE_URL"]
    if "GUILDHALL_DEMAND_REFRESH_MINUTES" in env:
        app_cfg["demand_refresh_minutes"] = int(env["GUILDHALL_DEMAND_REFRESH_MINUTES"])
    if "GUILDHALL_AH_DEPOSIT_PERCENT" in env:
        app_cfg["ah_deposit_percent"] = float(env["GUILDHALL_AH_DEPOSIT_PERCENT"])
    if "GUILDHALL_AH_DEPOSIT_RATE" in env:
        app_cfg["ah_deposit_rate"] = float(env["GUILDHALL_AH_DEPOSIT_RATE"])
    if "GUILDHALL_AH_REFRESH_SECONDS" in env:
        app_cfg["ah_refresh_seconds"] = int(env["GUILDHALL_AH_REFRESH_SECONDS"])
    if "GUILDHALL_DOWNLOADS_DIR" in env:
        app_cfg["downloads_dir"] = env["GUILDHALL_DOWNLOADS_DIR"]
    if "GUILDHALL_DOWNLOADS_INTERNAL_PREFIX" in env:
        app_cfg["downloads_internal_prefix"] = env["GUILDHALL_DOWNLOADS_INTERNAL_PREFIX"]

    if "GUILDHALL_DB_HOST" in env:
        db_cfg["host"] = env["GUILDHALL_DB_HOST"]
    if "GUILDHALL_DB_PORT" in env:
        db_cfg["port"] = int(env["GUILDHALL_DB_PORT"])
    if "GUILDHALL_DB_USER" in env:
        db_cfg["user"] = env["GUILDHALL_DB_USER"]
    if "GUILDHALL_DB_PASSWORD" in env:
        db_cfg["password"] = env["GUILDHALL_DB_PASSWORD"]
    if "GUILDHALL_DB_POOL_SIZE" in env:
        db_cfg["pool_size"] = int(env["GUILDHALL_DB_POOL_SIZE"])

    if not app_cfg.get("secret_key"):
        raise RuntimeError("GUILDHALL_SECRET_KEY is required")
    for required in ("host", "user", "password"):
        if not db_cfg.get(required):
            raise RuntimeError(f"GUILDHALL_DB_{required.upper()} is required")
    return cfg


def create_app() -> Flask:
    cfg = load_config()
    db.init_pool(cfg["database"])

    app = Flask(__name__)
    app_cfg = cfg.get("app", {})
    app.secret_key = app_cfg["secret_key"]
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app_cfg.get("behind_tls", False),
        INVITE_TOKENS_DEFAULT=app_cfg.get("invite_tokens_default", 3),
        INVITE_TTL_HOURS=app_cfg.get("invite_ttl_hours", 12),
        NEW_ACCOUNT_EXPANSION=app_cfg.get("new_account_expansion", 2),
        ADMIN_GMLEVEL=app_cfg.get("admin_gmlevel", 3),
        PUBLIC_BASE_URL=(app_cfg.get("public_base_url") or "").rstrip("/"),
        DEMAND_REFRESH_MINUTES=app_cfg.get("demand_refresh_minutes", 360),
        # Auction deposit preview (authoritative value is computed server-side).
        AH_DEPOSIT_PERCENT=app_cfg.get("ah_deposit_percent", 5.0),
        AH_DEPOSIT_RATE=app_cfg.get("ah_deposit_rate", 1.0),
        # Min seconds between user-triggered inventory refreshes (force-saves),
        # per character.
        AH_REFRESH_SECONDS=app_cfg.get("ah_refresh_seconds", 60),
        DOWNLOADS_DIR=app_cfg.get("downloads_dir", "/media/plex/downloads"),
        # Internal nginx location to hand large files off to via X-Accel-Redirect.
        # Empty = serve through Flask (fine for the dev server / small files).
        DOWNLOADS_INTERNAL_PREFIX=(app_cfg.get("downloads_internal_prefix") or ""),
    )

    # Behind nginx: trust one proxy hop so request.remote_addr (rate limiting)
    # and the URL scheme/Secure cookie reflect the real client, not the proxy.
    if app_cfg.get("trust_proxy", False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # AI news desk (configured from GUILDHALL_GEMINI_* env vars). If no key /
    # SDK, it stays dark and the News tab shows an offline notice.
    app.config["_news_desk"] = NewsDesk.from_env()

    _register_security(app)
    _register_filters(app)
    _register_routes(app)

    @app.context_processor
    def inject_nav():
        return {
            "is_admin": current_is_admin(),
            "characters": current_characters(),
            "active_character": active_character(),
        }

    app.config["_rate"] = {}  # ip -> [(timestamp), ...]
    return app


# ---------------------------------------------------------------------------
# Security: headers, CSRF, simple rate limiting
# ---------------------------------------------------------------------------
def _register_security(app: Flask) -> None:
    # Wowhead item tooltips need their external widget script + data host, and the
    # widget injects inline styles -- hence the loosened script/style/connect-src.
    csp = (
        "default-src 'self'; "
        "script-src 'self' https://wow.zamimg.com https://nether.wowhead.com https://www.wowhead.com; "
        "style-src 'self' 'unsafe-inline' https://wow.zamimg.com; "
        "img-src 'self' data: https://wow.zamimg.com; "
        "connect-src 'self' https://nether.wowhead.com https://www.wowhead.com; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )

    @app.after_request
    def set_headers(resp):
        resp.headers["Content-Security-Policy"] = csp
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    def csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(sent, expected):
                abort(400, "Invalid or missing CSRF token.")


def _rate_limited(app: Flask, key: str, limit: int, window: int) -> bool:
    """Return True if ``key`` has exceeded ``limit`` hits within ``window`` secs."""
    now = time.time()
    bucket = app.config["_rate"].setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


# ---------------------------------------------------------------------------
# Jinja filters
# ---------------------------------------------------------------------------
def _register_filters(app: Flask) -> None:
    @app.template_filter("nl2br")
    def nl2br(value: str) -> Markup:
        # Escape FIRST, then join escaped segments with a trusted <br>.
        return Markup("<br>").join(escape(value).split("\n"))

    @app.template_filter("profession")
    def profession(skill_id: int) -> str:
        return profession_name(skill_id)

    @app.template_filter("icon_url")
    def icon_url(name: str, size: str = "medium") -> str:
        # Wowhead icon CDN; sizes: small (18), medium (36), large (56).
        return f"https://wow.zamimg.com/images/wow/icons/{size}/{name or 'inv_misc_questionmark'}.jpg"

    @app.template_filter("money")
    def money(copper) -> Markup:
        # Render a copper amount as coloured g/s/c coins (WoW convention).
        c = int(copper or 0)
        gold, rem = divmod(c, 10000)
        silver, copper_ = divmod(rem, 100)
        parts = []
        if gold:
            parts.append(f'<span class="coin gold">{gold:,}</span>')
        if silver or gold:
            parts.append(f'<span class="coin silver">{silver}</span>')
        parts.append(f'<span class="coin copper">{copper_}</span>')
        return Markup('<span class="money">' + "".join(parts) + "</span>")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "account_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def current_is_admin() -> bool:
    """Whether the logged-in account is an admin (gmlevel >= configured), cached
    per request on flask.g."""
    if "account_id" not in session:
        return False
    if not hasattr(g, "_is_admin"):
        from flask import current_app
        g._is_admin = db.is_admin(
            session["account_id"], current_app.config["ADMIN_GMLEVEL"]
        )
    return g._is_admin


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "account_id" not in session:
            return redirect(url_for("login", next=request.path))
        if not current_is_admin():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def current_characters():
    """The logged-in account's characters (request-cached on flask.g)."""
    if "account_id" not in session:
        return []
    if not hasattr(g, "_chars"):
        g._chars = db.account_characters(session["account_id"])
    return g._chars


def active_character():
    """The character the player is 'browsing as' -- their session selection if
    valid, else the highest-level guilded character (else highest-level)."""
    chars = current_characters()
    if not chars:
        return None
    selected = session.get("char_guid")
    if selected:
        for ch in chars:
            if ch["guid"] == selected:
                return ch
    for ch in chars:               # default: highest-level guilded character
        if ch["guildid"]:
            return ch
    return chars[0]


def current_guild():
    """Guild context of the active character, or None if it isn't in a guild.
    Keeps the same shape the routes/templates expect."""
    ch = active_character()
    if not ch or not ch["guildid"]:
        return None
    return {
        "guildid": ch["guildid"],
        "guild_name": ch["guild_name"],
        "char_guid": ch["guid"],
        "char_name": ch["name"],
        "is_leader": ch["guid"] == ch["leaderguid"],
    }


# The market sections of the news desk, in display order. Heroic Exploits is
# deliberately omitted for now (player-activity stories come later).
NEWS_MARKET_CATEGORIES = (
    news_prompts.PROFESSIONAL_DIGEST,
    news_prompts.GEAR_FOR_YOU,
    news_prompts.PRIMARY_STATS,
)


def todays_news(desk: NewsDesk, events: dict | None) -> list[dict]:
    """Today's market articles, cache-first. For each market category, return the
    cached article for today's event date, generating (and caching) it on a miss.

    Returns [] when there is no active market event or the desk is offline with
    nothing cached -- the page renders an empty state in that case.
    """
    if not events or not (events.get("enabled") or events.get("discount_enabled")):
        return []
    event_date = events.get("date")
    cached = {row["category"]: row for row in db.news_get(event_date)}
    out: list[dict] = []
    for cat in NEWS_MARKET_CATEGORIES:
        article = cached.get(cat)
        if article is None and desk.available():
            # Seed the reporter pick per (date, category) so the byline is stable
            # all day but the three sections don't all land on the same reporter.
            article = desk.generate_market_article(
                cat, events, seed=f"{event_date}:{cat}")
            if article:
                db.news_store(event_date, article)
        if article:
            out.append(article)
    return out


def _exploit_columns(edition_date: str) -> tuple[list[dict], list[dict]]:
    """Split a date's cached exploit_news rows into (heroic exploits, obituaries).
    Read-only -- works for today's edition or any archived one. Obituary rows are
    keyed "d<guid>" (see exploits.is_obituary); everything else is an exploit."""
    def art(r, category):
        return {
            "category": category, "subject": r["subject"],
            "headline": r["headline"], "dek": r["dek"],
            "content": r["content"], "author": r["author"],
            "author_title": r["author_title"], "dateline": r["dateline"],
        }

    exploit_articles, obituary_articles = [], []
    for r in db.exploit_news_get(edition_date):
        if r["story_key"].startswith("d"):
            obituary_articles.append(art(r, news_prompts.OBITUARIES))
        else:
            exploit_articles.append(art(r, news_prompts.HEROIC_EXPLOITS))
    return exploit_articles, obituary_articles


def _edition_neighbors(edition_date: str) -> tuple[str | None, str | None]:
    """(previous_older, next_newer) edition dates that actually exist, or None.
    ``db.edition_dates`` is newest-first, so the closest older date is the first
    below ``edition_date`` and the closest newer is the last above it."""
    dates = db.edition_dates()
    older = [d for d in dates if d < edition_date]
    newer = [d for d in dates if d > edition_date]
    return (older[0] if older else None, newer[-1] if newer else None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _register_routes(app: Flask) -> None:
    @app.route("/")
    @login_required
    def dashboard():
        return render_template(
            "dashboard.html",
            username=session.get("username"),
            guild=current_guild(),
        )

    # --- auth -------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            ip = request.remote_addr or "unknown"
            if _rate_limited(app, f"login:{ip}", limit=10, window=300):
                flash("Too many attempts. Please wait a few minutes.", "error")
                return render_template("login.html"), 429

            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            account = db.get_account_by_username(username)
            if account and srp6.check_login(
                account["username"], password,
                account["salt"], account["verifier"],
            ):
                session.clear()  # guard against session fixation
                session["account_id"] = account["id"]
                session["username"] = account["username"]
                dest = request.args.get("next", "")
                if not dest.startswith("/"):
                    dest = url_for("dashboard")
                return redirect(dest)
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/character/select", methods=["POST"])
    @login_required
    def select_character():
        guid = request.form.get("guid", type=int)
        if guid and any(ch["guid"] == guid for ch in current_characters()):
            session["char_guid"] = guid
        dest = request.referrer
        if not dest or not dest.startswith(request.host_url):
            dest = url_for("dashboard")
        return redirect(dest)

    # --- password ---------------------------------------------------------
    @app.route("/password", methods=["GET", "POST"])
    @login_required
    def password():
        if request.method == "POST":
            account_id = session["account_id"]  # identity from session only
            ip = request.remote_addr or "unknown"
            if _rate_limited(app, f"pw:{account_id}:{ip}", limit=5, window=300):
                flash("Too many attempts. Please wait a few minutes.", "error")
                return render_template("password.html"), 429

            current = request.form.get("current_password") or ""
            new = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""

            account = db.get_account_by_id(account_id)
            if not account or not srp6.check_login(
                account["username"], current,
                account["salt"], account["verifier"],
            ):
                flash("Current password is incorrect.", "error")
            elif new != confirm:
                flash("New passwords do not match.", "error")
            elif not 1 <= len(new) <= MAX_PASS_LEN:
                flash(f"Password must be 1-{MAX_PASS_LEN} characters.", "error")
            else:
                salt, verifier = srp6.make_registration_data(
                    account["username"], new
                )
                db.update_password(account_id, salt, verifier)
                flash("Password changed. Use it next time you log in.", "ok")
                return redirect(url_for("password"))
        return render_template("password.html")

    # --- downloads --------------------------------------------------------
    @app.route("/chronicle")
    @login_required
    def chronicle():
        from itertools import groupby

        ch = active_character()
        events = db.chronicle_events(ch["guid"]) if ch else []
        # Rows arrive newest-first; rows for a given day are contiguous, so a
        # simple groupby yields the day-by-day timeline the template renders.
        days = [
            {"date": day, "events": list(items)}
            for day, items in groupby(events, key=lambda e: e["event_time"].date())
        ]
        return render_template("chronicle.html", character=ch, days=days)

    @app.route("/downloads")
    @login_required
    def downloads():
        files = list_downloads(app.config["DOWNLOADS_DIR"])
        return render_template("downloads.html", files=files)

    @app.route("/downloads/<name>")
    @login_required
    def download_file(name):
        target = resolve_download(app.config["DOWNLOADS_DIR"], name)
        if target is None:
            abort(404)

        prefix = app.config["DOWNLOADS_INTERNAL_PREFIX"]
        if prefix:
            # Hand the byte-pushing to nginx (sendfile + range/resume support):
            # Flask only authorized the request. The internal location must map
            # the same DOWNLOADS_DIR. Encode the name so spaces/specials survive.
            resp = Response()
            resp.headers["X-Accel-Redirect"] = prefix.rstrip("/") + "/" + quote(target.name)
            resp.headers["Content-Type"] = "application/octet-stream"
            resp.headers["Content-Disposition"] = (
                f"attachment; filename*=UTF-8''{quote(target.name)}"
            )
            return resp
        # Dev / small-file fallback: stream through Flask (supports Range too).
        return send_file(
            target, as_attachment=True, download_name=target.name,
            conditional=True,
        )

    # --- auction house ----------------------------------------------------
    @app.route("/auctionhouse")
    @login_required
    def auctionhouse():
        ch = active_character()
        sections = {"inventory": [], "bank": []}
        totals = {"inventory": 0, "bank": 0}
        if ch:
            rows = db.character_inventory_breakdown(ch["guid"])
            sections, totals = build_ah_view(rows)
        return render_template(
            "auctionhouse.html",
            character=ch,
            sections=sections,
            totals=totals,
            grand_total=totals["inventory"] + totals["bank"],
            service_up=ahservice.available(),
            ah_events=ahservice.events(),
            listing_enabled=soap.enabled(),
            char_online=bool(ch and ch.get("online")),
            # "Refresh inventory" forces a server save; only useful when the
            # character is online and only when SOAP is wired up.
            can_refresh=bool(ch and soap.enabled()),
        )

    @app.route("/auctionhouse/refresh", methods=["POST"])
    @login_required
    def auctionhouse_refresh():
        ch = active_character()
        if not ch:
            return redirect(url_for("auctionhouse"))
        if not soap.enabled():
            flash("Refreshing isn't available right now.", "error")
            return redirect(url_for("auctionhouse"))
        # Rate-limit per character (configurable; default once a minute) so a user
        # can't hammer the world thread with force-saves.
        window = app.config["AH_REFRESH_SECONDS"]
        if _rate_limited(app, f"ahrefresh:{ch['guid']}", limit=1, window=window):
            flash(f"Just refreshed -- please wait up to {window}s before "
                  "refreshing again.", "error")
            return redirect(url_for("auctionhouse"))
        ok, out = soap.command(f"guildhall save {ch['guid']}")
        _flash_refresh_result(ok, out)
        return redirect(url_for("auctionhouse"))

    @app.route("/auctionhouse/review", methods=["POST"])
    @login_required
    def auctionhouse_review():
        ch = active_character()
        if not ch:
            return redirect(url_for("auctionhouse"))
        if not soap.enabled():
            flash("Selling from the web isn't available right now.", "error")
            return redirect(url_for("auctionhouse"))

        selected = _posted_guids(request.form.getlist("row"))
        by_guid = _sellable_by_guid(ch["guid"])
        instances = db.held_item_instances(ch["guid"])
        # Group the still-valid selected stacks back into one editable line per
        # item (entry + rolled property), priced from the live inventory.
        groups: dict[tuple, dict] = {}
        for g in selected:
            it = by_guid.get(g)
            if not it or g not in instances:
                continue  # sold / moved / no longer sellable since the AH page
            # Group by item + whether it sells at vendor price, so a soulbound (or
            # otherwise vendor-priced) copy never merges with an auctionable one.
            key = (it["entry"], it["random"], it["vendor_priced"])
            grp = groups.setdefault(key, {
                "entry": it["entry"], "name": it["name"], "icon": it["icon"],
                "quality": it["quality"], "unit": it["unit"] or 0,
                "vendor_priced": it["vendor_priced"],
                "guids": [], "count": 0, "stacks": 0,
            })
            grp["guids"].append(g)
            grp["count"] += int(instances[g]["count"])
            grp["stacks"] += 1

        rows = sorted(groups.values(), key=lambda r: r["name"].lower())
        if not rows:
            flash("None of the selected items can be sold anymore.", "error")
            return redirect(url_for("auctionhouse"))

        entries = [r["entry"] for r in rows]
        sell_prices = db.item_sell_prices(entries)
        max_stacks = db.item_max_stacks(entries)
        # Vendor-priced items (the market won't beat the vendor, or they're
        # soulbound and can't be auctioned at all) are instant-sold to the vendor
        # on submit rather than auctioned. build_ah_view already decided this per
        # item; trust that flag here.
        auction_rows, vendor_rows = [], []
        for r in rows:
            r["guids_csv"] = ",".join(str(g) for g in r["guids"])
            r["guid_counts_csv"] = ",".join(
                str(instances[g]["count"]) for g in r["guids"])
            r["sell_price"] = sell_prices.get(r["entry"], 0)
            r["unit_price"] = r["unit"] or 0
            if r["vendor_priced"]:
                # Vendor (instant-sell) row: fixed price, not editable/auctioned.
                r["vendor_unit"] = r["sell_price"] or r["unit_price"]
                r["vendor_total"] = r["vendor_unit"] * r["count"]
                vendor_rows.append(r)
                continue
            # WoW-style stack-size x #stacks. Default to the largest legal stack and
            # as many whole stacks as the held quantity allows.
            r["max_stack"] = max(1, min(max_stacks.get(r["entry"], 1), r["count"]))
            r["def_stack"] = r["max_stack"]
            r["def_num"] = max(1, r["count"] // r["def_stack"])
            # Prefilled prices are per single item; the form edits per-stack totals.
            r["bid_gsc"] = _to_gsc(r["unit_price"] * r["def_stack"])
            r["buyout_gsc"] = _to_gsc(r["unit_price"] * r["def_stack"])
            auction_rows.append(r)

        return render_template(
            "auction_review.html",
            character=ch,
            rows=auction_rows,
            vendor_rows=vendor_rows,
            durations=AUCTION_DURATIONS,
            deposit_percent=app.config["AH_DEPOSIT_PERCENT"],
            deposit_rate=app.config["AH_DEPOSIT_RATE"],
            char_online=bool(ch.get("online")),
            money=int(ch.get("money") or 0),
        )

    @app.route("/auctionhouse/list", methods=["POST"])
    @login_required
    def auctionhouse_list():
        ch = active_character()
        if not ch:
            return redirect(url_for("auctionhouse"))
        if not soap.enabled():
            flash("Selling from the web isn't available right now.", "error")
            return redirect(url_for("auctionhouse"))

        # The page submits one JSON object that keeps the two actions separate:
        #   {"hours": 12,
        #    "auctions": [{"guids","stack_size","num_stacks","bid","buyout"}, ...],
        #    "vendor":   ["guid,guid", ...]}
        # A duration is only required when there are auctions; a vendor-only
        # submission needs no duration at all.
        try:
            payload = json.loads(request.form.get("payload") or "{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, TypeError):
            flash("Couldn't read the submission. Please try again.", "error")
            return redirect(url_for("auctionhouse"))

        auctions = payload.get("auctions") or []
        vendor = payload.get("vendor") or []
        try:
            hours = int(payload.get("hours") or 0)
        except (ValueError, TypeError):
            hours = 0
        if auctions and hours not in AUCTION_DURATIONS:
            flash("Pick a valid auction duration.", "error")
            return redirect(url_for("auctionhouse"))

        by_guid = _sellable_by_guid(ch["guid"])
        instances = db.held_item_instances(ch["guid"])

        def _live_guids(csv):
            """Guids from a CSV that are still held & sellable, with their total
            available count."""
            guids, available = [], 0
            for part in str(csv).split(","):
                if not part.isdigit():
                    continue
                g = int(part)
                if g not in by_guid:  # no longer held / sellable
                    continue
                guids.append(g)
                available += int((instances.get(g) or {}).get("count", 1))
            return guids, available

        # Each auction posts one item as WoW does: stack size x number of stacks,
        # with the bid/buyout per *stack* (already copper in the payload).
        specs: list[str] = []
        for a in auctions if isinstance(auctions, list) else []:
            if not isinstance(a, dict):
                continue
            try:
                stack_size = int(a.get("stack_size"))
                num_stacks = int(a.get("num_stacks"))
                bid_stack = max(0, int(a.get("bid") or 0))
                buyout_stack = max(0, int(a.get("buyout") or 0))
            except (ValueError, TypeError):
                continue
            if stack_size < 1 or num_stacks < 1 or bid_stack <= 0:
                continue  # need a positive size, count and starting bid
            guids, available = _live_guids(a.get("guids", ""))
            if not guids or stack_size * num_stacks > available:
                continue
            specs.append(f"{stack_size}:{num_stacks}:{bid_stack}:{buyout_stack}:"
                         + ",".join(str(g) for g in guids))

        # Vendor (instant-sell) rows: the server re-validates each CSV and prices
        # the items at their vendor SellPrice.
        vendor_specs: list[str] = []
        for csv in vendor if isinstance(vendor, list) else []:
            guids, _ = _live_guids(csv)
            if guids:
                vendor_specs.append(",".join(str(g) for g in guids))

        if not specs and not vendor_specs:
            flash("Nothing valid to list -- prices or items may have changed.", "error")
            return redirect(url_for("auctionhouse"))

        if specs:
            ok, out = soap.command(
                f"guildhall list {ch['guid']} {hours} " + " ".join(specs))
            _flash_listing_result(ok, out)
        if vendor_specs:
            ok, out = soap.command(
                f"guildhall vendor {ch['guid']} " + " ".join(vendor_specs))
            _flash_vendor_result(ok, out)
        return redirect(url_for("auctionhouse"))

    # --- news -------------------------------------------------------------
    @app.route("/news")
    @login_required
    def news():
        from datetime import date

        from flask import current_app
        desk = current_app.config["_news_desk"]
        events = ahservice.events()
        articles = todays_news(desk, events)
        today = date.today().isoformat()
        # Heroic Exploits and Obituaries are generated only by the daily
        # scheduler (from the chronicle); the page just reads today's cached
        # stories.
        exploit_articles, obituary_articles = _exploit_columns(today)
        prev_date, _ = _edition_neighbors(today)
        return render_template(
            "news.html",
            articles=articles,
            exploit_articles=exploit_articles,
            obituary_articles=obituary_articles,
            events=events,
            edition_date=today,
            is_today=True,
            prev_date=prev_date,
            next_date=None,
            desk_available=desk.available(),
            category_titles=news_prompts.CATEGORY_TITLES,
        )

    @app.route("/news/<edition>")
    @login_required
    def news_archive(edition):
        """A past day's edition, read-only -- whatever was cached for that date.
        Today (or anything not yet past) redirects to the live page, which can
        still generate missing stories."""
        from datetime import date

        try:
            edition = date.fromisoformat(edition).isoformat()
        except ValueError:
            abort(404)
        if edition >= date.today().isoformat():
            return redirect(url_for("news"))

        articles = db.news_get(edition)
        exploit_articles, obituary_articles = _exploit_columns(edition)
        prev_date, next_date = _edition_neighbors(edition)
        return render_template(
            "news.html",
            articles=articles,
            exploit_articles=exploit_articles,
            obituary_articles=obituary_articles,
            events=None,
            edition_date=edition,
            is_today=False,
            prev_date=prev_date,
            next_date=next_date,
            desk_available=True,  # archive is read-only; not a desk-status page
            category_titles=news_prompts.CATEGORY_TITLES,
        )

    # --- forum ------------------------------------------------------------
    @app.route("/forum")
    @login_required
    def forum():
        guild = current_guild()
        counts = db.feed_counts(guild["guildid"]) if guild else {}
        return render_template("forum_index.html", guild=guild, counts=counts)

    @app.route("/forum/<feed>", methods=["GET", "POST"])
    @login_required
    def forum_feed(feed):
        if feed not in VALID_FEEDS:
            abort(404)
        guild = current_guild()
        if not guild:
            flash("Join a guild in-game to use the forum.", "error")
            return redirect(url_for("forum"))

        if request.method == "POST":
            if feed == "official" and not guild["is_leader"]:
                abort(403)
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            if not title or not body:
                flash("Title and message are required.", "error")
            elif len(title) > MAX_TITLE_LEN or len(body) > MAX_BODY_LEN:
                flash("Title or message is too long.", "error")
            else:
                db.create_post(
                    guild["guildid"], feed, guild["char_guid"], title, body
                )
                flash("Posted.", "ok")
                return redirect(url_for("forum_feed", feed=feed))

        posts = db.list_feed(guild["guildid"], feed)
        can_post = feed == "player" or guild["is_leader"]
        return render_template(
            "forum_feed.html", feed=feed, posts=posts,
            guild=guild, can_post=can_post,
        )

    @app.route("/forum/post/<int:post_id>")
    @login_required
    def forum_post(post_id):
        guild = current_guild()
        post = db.get_post(post_id)
        if not post or not guild or post["guildid"] != guild["guildid"]:
            abort(404)  # not yours to see -> indistinguishable from missing
        replies = db.list_replies(post_id)
        return render_template(
            "forum_post.html", post=post, replies=replies, guild=guild
        )

    @app.route("/forum/post/<int:post_id>/reply", methods=["POST"])
    @login_required
    def forum_reply(post_id):
        guild = current_guild()
        post = db.get_post(post_id)
        if not post or not guild or post["guildid"] != guild["guildid"]:
            abort(404)
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Reply cannot be empty.", "error")
        elif len(body) > MAX_BODY_LEN:
            flash("Reply is too long.", "error")
        else:
            db.create_reply(post_id, guild["char_guid"], body)
        return redirect(url_for("forum_post", post_id=post_id))

    @app.route("/forum/post/<int:post_id>/delete", methods=["POST"])
    @login_required
    def forum_post_delete(post_id):
        guild = current_guild()
        post = db.get_post(post_id)
        if not post or not guild or post["guildid"] != guild["guildid"]:
            abort(404)
        if not _can_moderate(guild, post["author_guid"]):
            abort(403)
        db.delete_post(post_id)
        flash("Post deleted.", "ok")
        return redirect(url_for("forum_feed", feed=post["feed"]))

    @app.route("/forum/reply/<int:reply_id>/delete", methods=["POST"])
    @login_required
    def forum_reply_delete(reply_id):
        guild = current_guild()
        reply = db.get_reply(reply_id)
        if not reply or not guild or reply["guildid"] != guild["guildid"]:
            abort(404)
        if not _can_moderate(guild, reply["author_guid"]):
            abort(403)
        db.delete_reply(reply_id)
        flash("Reply deleted.", "ok")
        return redirect(url_for("forum_post", post_id=reply["post_id"]))

    # --- roster -----------------------------------------------------------
    @app.route("/roster")
    @login_required
    def roster():
        guild = current_guild()
        # skill_id -> list of members with that profession (skill desc).
        by_skill: dict = {}
        member_demand: dict = {}
        surplus: list = []
        if guild:
            member_guids = set()
            names: dict = {}
            for row in db.guild_professions(guild["guildid"]):
                by_skill.setdefault(row["skill"], []).append(row)
                member_guids.add(row["char_guid"])
                names[row["char_guid"]] = row["char_name"]
            for members in by_skill.values():
                members.sort(key=lambda m: (-m["value"], m["char_name"].lower()))
            # Per-member in-demand (for the per-character dropdown rows).
            member_demand = {guid: _demand_for(guid) for guid in member_guids}

            # Surplus: items the active char holds that others need but they don't.
            active = guild["char_guid"]
            demanded_by: dict = {}      # item_id -> {id,name,icon,needers:[...]}
            for guid, skills in member_demand.items():
                if guid == active:
                    continue
                # which professions (skills) this member needs each item for
                per_item: dict = {}
                for skill, lst in skills.items():
                    for it in lst:
                        e = per_item.setdefault(
                            it["id"], {"name": it["name"],
                                       "icon": it["icon"], "skills": set()})
                        e["skills"].add(skill)
                for iid, info in per_item.items():
                    d = demanded_by.setdefault(
                        iid, {"id": iid, "name": info["name"],
                              "icon": info["icon"], "needers": []})
                    d["needers"].append({
                        "name": names.get(guid, "?"), "guid": guid,
                        "professions": [profession_name(s)
                                        for s in sorted(info["skills"])],
                    })
            own = member_demand.get(active, {})
            own_ids = {it["id"] for lst in own.values() for it in lst}
            held = db.character_held_items(active)
            surplus = [
                demanded_by[iid] for iid in held
                if iid in demanded_by and iid not in own_ids
            ]
            for s in surplus:
                s["needers"].sort(key=lambda n: n["name"].lower())
            surplus.sort(key=lambda s: (-len(s["needers"]), s["name"]))

        # Group professions into Crafting / Gathering / General categories.
        categories = []
        for cat in professions.CATEGORY_ORDER:
            profs = [
                {"skill": s, "name": profession_name(s), "members": by_skill[s]}
                for s in sorted(by_skill, key=profession_name)
                if professions.category_of(s) == cat
            ]
            if profs:
                categories.append({"name": cat, "professions": profs})
        return render_template(
            "roster.html", guild=guild, categories=categories,
            member_demand=member_demand, surplus=surplus,
        )

    def _demand_for(guid):
        """Per-profession demand for a character (cache-first; computes on miss).
        Returns {skill_id: [items]}. Used to aggregate guild-wide on the roster."""
        row = db.demand_get(guid)
        ttl = app.config["DEMAND_REFRESH_MINUTES"]
        if row and row["age_minutes"] is not None and row["age_minutes"] < ttl:
            return {int(k): v for k, v in json.loads(row["items"]).items()}
        profs = db.character_professions(guid)
        skill_values = {p["skill"]: p["value"] for p in profs}
        known = db.character_known_spell_ids(guid)
        demand, _ = _player_demand(guid, skill_values, known)
        return demand

    def _player_demand(guid, skill_values, known):
        """Per-profession in-demand base mats, cached and recomputed past the TTL.
        Returns ({skill_id: [items]}, computed_at)."""
        row = db.demand_get(guid)
        ttl = app.config["DEMAND_REFRESH_MINUTES"]
        if row and row["age_minutes"] is not None and row["age_minutes"] < ttl:
            data = json.loads(row["items"])
            return {int(k): v for k, v in data.items()}, row["computed_at"]
        # "need" = how many of that profession's skill-up recipes use the base
        # item (crafted intermediates decomposed, vendor items excluded).
        by_prof = recipes.reagent_demand_by_profession(known, skill_values)
        out: dict = {}
        for skill, items in by_prof.items():
            lst = [
                {"id": iid, "name": info["name"], "icon": info.get("icon", ""),
                 "need": info["count"]}
                for iid, info in items.items()
            ]
            lst.sort(key=lambda d: (-d["need"], d["name"]))
            out[skill] = lst
        db.demand_store(guid, json.dumps({str(k): v for k, v in out.items()}))
        from datetime import datetime
        return out, datetime.now()

    @app.route("/roster/player/<int:guid>")
    @login_required
    def player(guid):
        guild = current_guild()
        target = db.guild_member_char(guid)
        # Only viewable if the target shares the viewer's guild.
        if not guild or not target or target["guildid"] != guild["guildid"]:
            abort(404)
        profs = db.character_professions(guid)
        skill_values = {p["skill"]: p["value"] for p in profs}
        known = db.character_known_spell_ids(guid)
        recipe_groups = recipes.recipes_for_known(known, skill_values)
        demand_by_prof, demand_at = _player_demand(guid, skill_values, known)

        # Group the recipe sections by category for display.
        cats: dict = {}
        for g in recipe_groups:
            cats.setdefault(professions.category_of(g["skill"]), []).append(g)
        recipe_categories = [
            {"name": c, "groups": cats[c]}
            for c in professions.CATEGORY_ORDER if c in cats
        ]
        return render_template(
            "player.html", target=target, profs=profs,
            recipe_categories=recipe_categories, recipes_available=recipes.available(),
            demand_by_prof=demand_by_prof, demand_at=demand_at,
        )

    # --- invites (authenticated) -----------------------------------------
    def _render_invites(new_link=None):
        account_id = session["account_id"]
        return render_template(
            "invites.html",
            tokens=db.invite_available_tokens(account_id, app.config["INVITE_TOKENS_DEFAULT"]),
            invites=db.invite_list_for(account_id),
            ttl_hours=app.config["INVITE_TTL_HOURS"],
            new_link=new_link,
        )

    @app.route("/invites", methods=["GET", "POST"])
    @login_required
    def invites():
        account_id = session["account_id"]
        if request.method == "POST":
            tokens = db.invite_available_tokens(account_id, app.config["INVITE_TOKENS_DEFAULT"])
            if tokens["available"] <= 0:
                flash("You have no invite tokens available.", "error")
                return redirect(url_for("invites"))
            token = secrets.token_urlsafe(32)
            db.invite_create(account_id, _token_hash(token), app.config["INVITE_TTL_HOURS"])
            # Prefer an explicit public base URL (correct behind nginx / with a port);
            # fall back to the request host if it isn't configured.
            base = app.config["PUBLIC_BASE_URL"]
            path = url_for("invite_redeem", token=token)
            link = (base + path) if base else url_for(
                "invite_redeem", token=token, _external=True
            )
            flash("Invite created — copy the link now; it is shown only once.", "ok")
            return _render_invites(new_link=link)
        return _render_invites()

    @app.route("/invites/<int:invite_id>/revoke", methods=["POST"])
    @login_required
    def revoke_invite(invite_id):
        if db.invite_revoke(invite_id, session["account_id"]):
            flash("Invite canceled; token refunded.", "ok")
        return redirect(url_for("invites"))

    # --- invite redemption (PUBLIC, unauthenticated) ---------------------
    @app.route("/invite/<token>", methods=["GET", "POST"])
    def invite_redeem(token):
        token_hash = _token_hash(token)
        if request.method == "POST":
            ip = request.remote_addr or "unknown"
            if _rate_limited(app, f"register:{ip}", limit=10, window=600):
                flash("Too many attempts. Please wait a few minutes.", "error")
                return render_template("invite_register.html", token=token), 429

            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""
            email = (request.form.get("email") or "").strip()

            error = None
            if not USERNAME_RE.match(username):
                error = "Username must be 1-17 characters: letters, digits, _ . -"
            elif not 1 <= len(password) <= MAX_PASS_LEN:
                error = f"Password must be 1-{MAX_PASS_LEN} characters."
            elif password != confirm:
                error = "Passwords do not match."
            elif len(email) > MAX_EMAIL_LEN:
                error = "Email address is too long."
            if error:
                flash(error, "error")
                return render_template("invite_register.html", token=token)

            # Normalize exactly like AccountMgr::CreateAccount (upper-case Latin).
            u, p, e = username.upper(), password.upper(), email.upper()
            salt, verifier = srp6.make_registration_data(u, p)
            try:
                db.redeem_invite_and_create_account(
                    token_hash, u, salt, verifier,
                    app.config["NEW_ACCOUNT_EXPANSION"], e,
                )
            except db.InviteError as exc:
                if exc.code == "taken":
                    flash("That username is already taken.", "error")
                    return render_template("invite_register.html", token=token)
                return render_template("invite_done.html", state="invalid")
            return render_template("invite_done.html", state="created", username=u)

        # GET: show the form only for a live link.
        if not db.invite_is_redeemable(token_hash):
            return render_template("invite_done.html", state="invalid")
        return render_template("invite_register.html", token=token)

    # --- admin: invite-token allowances ----------------------------------
    @app.route("/admin/tokens", methods=["GET", "POST"])
    @admin_required
    def admin_tokens():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip().upper()
            action = request.form.get("action", "set")
            account_id = db.account_id_by_username(username)
            if not account_id:
                flash(f"No account named '{username}'.", "error")
            elif action == "remove":
                db.allowance_remove(account_id)
                flash(f"Removed override for {username}; back to default.", "ok")
            else:
                raw = (request.form.get("tokens") or "").strip()
                if not raw.isdigit():
                    flash("Tokens must be a non-negative whole number.", "error")
                else:
                    db.allowance_set(account_id, int(raw))
                    flash(f"Set {username}'s allowance to {int(raw)} tokens.", "ok")
            return redirect(url_for("admin_tokens"))
        return render_template(
            "admin_tokens.html",
            overrides=db.allowance_list(),
            default_tokens=app.config["INVITE_TOKENS_DEFAULT"],
        )


def _can_moderate(guild: dict, author_guid: int) -> bool:
    """A post/reply may be removed by its author or by the guild leader."""
    return guild["is_leader"] or author_guid == guild["char_guid"]


# Inventory slot ranges (from Player.h). Top-level rows have bag = 0; their slot
# says where they sit. Nested rows (bag != 0) live inside a container whose
# top-level slot decides whether it is an inventory bag or a bank bag.
_INV_BAG_SLOTS = range(19, 23)     # equipped inventory bag containers
_BACKPACK_SLOTS = range(23, 39)    # the default backpack
_BANK_SLOTS = range(39, 67)        # main bank item slots
_BANK_BAG_SLOTS = range(67, 74)    # equipped bank bag containers
ITEM_FLAG_SOULBOUND = 0x1
ITEM_QUALITY_POOR = 0          # grey "junk" items (the in-game "sell junk" set)


def build_ah_view(rows: list[dict]):
    """Group a character's held items into Inventory and Bank sections priced at
    the realm AH bot's estimate. Equipped gear and the bag containers themselves
    are skipped. Returns ``(sections, totals)`` where sections is
    ``{"inventory": [...], "bank": [...]}`` and totals is the sellable-only sum
    of each section in copper."""
    # Pass 1: which region each equipped container belongs to.
    container_region: dict[int, str] = {}
    for r in rows:
        if r["bag"] == 0:
            if r["slot"] in _INV_BAG_SLOTS:
                container_region[r["item_guid"]] = "inventory"
            elif r["slot"] in _BANK_BAG_SLOTS:
                container_region[r["item_guid"]] = "bank"

    # Pass 2: assign each holdable item to a region and aggregate stacks.
    # key = (region, entry, randomPropertyId, soulbound) -> count. Random property
    # is part of the key because the AHPricingService prices a rolled suffix
    # ("... of the Bear") differently from the bare item.
    agg: dict[tuple, int] = {}
    agg_guids: dict[tuple, list[int]] = {}  # same key -> underlying item-instance guids
    for r in rows:
        if r["bag"] == 0:
            if r["slot"] in _BACKPACK_SLOTS:
                region = "inventory"
            elif r["slot"] in _BANK_SLOTS:
                region = "bank"
            else:
                continue  # equipped gear or an equipped bag/bank-bag container
        else:
            region = container_region.get(r["bag"])
            if region is None:
                continue
        sb = bool(r["flags"] & ITEM_FLAG_SOULBOUND)
        key = (region, r["itemEntry"], int(r["randomPropertyId"]), sb)
        agg[key] = agg.get(key, 0) + int(r["count"])
        agg_guids.setdefault(key, []).append(r["item_guid"])

    sections = {"inventory": [], "bank": []}
    totals = {"inventory": 0, "bank": 0}
    # Display info (name/quality/icon/ilvl) for the held entries; market prices come
    # from the AHPricingService (per entry+random property), vendor SellPrice from
    # item_template. An item "sells at vendor price" -- marked **Vendor Priced** and
    # instant-sold rather than auctioned -- when either the market won't beat the
    # vendor (the bot's buy price is floored at SellPrice, so buy <= SellPrice) or
    # the item is SOULBOUND (can't be auctioned at all, so selling = vendoring).
    entries = {entry for (_, entry, _, _) in agg}
    display = ahprices.display_for(entries)
    sell_prices = db.item_sell_prices(entries)
    svc_cache: dict[tuple, tuple] = {}  # (entry, random) -> (buy, bid, mult)
    for (region, entry, random_id, sb), count in agg.items():
        d = display.get(entry)
        sp = int(sell_prices.get(entry, 0) or 0)        # vendor SellPrice
        ck = (entry, random_id)
        if ck not in svc_cache:
            # side="buy": what the bot PAYS for the item -- what the player gets
            # selling it. (The list/sell price would be misleading here.)
            res = ahservice.price(entry, random_id, d["ilvl"] if d else 0,
                                  d["q"] if d else 0, side="buy")
            svc_cache[ck] = res if res else (None, None, None)
        buy = svc_cache[ck][0]
        mult = svc_cache[ck][2]

        if sb:
            # Soulbound: can't be auctioned, only vendored -> worth the vendor price.
            if sp > 0:
                unit, total, sellable, reason, multiplier = sp, sp * count, True, None, None
                vendor_priced = True
            else:
                unit, total, sellable, reason, multiplier = None, None, False, "Soulbound", None
                vendor_priced = False
        elif buy is None:
            # service doesn't cover it / unreachable -> no price (not sellable here)
            unit, total, sellable, reason, multiplier = None, None, False, "Not priced", None
            vendor_priced = False
        elif sp > 0 and buy <= sp:
            # Market won't beat the vendor -> sells at the vendor price (instant-sell).
            unit, total, sellable, reason, multiplier = sp, sp * count, True, None, None
            vendor_priced = True
        else:
            # A real auctionable market item; the daily-event swing applies.
            unit, total, sellable, reason, multiplier = buy, buy * count, True, None, mult
            vendor_priced = False
        quality = d["q"] if d else 1
        sections[region].append({
            "entry": entry,
            "name": d["n"] if d else f"Item #{entry}",
            "icon": d["icon"] if d else "",
            "quality": quality,
            "count": count,
            "random": bool(random_id),
            "unit": unit,
            "multiplier": multiplier,
            "vendor_priced": vendor_priced,
            "soulbound": sb,
            # "Junk" = Poor (grey) quality -- the game's own junk classification,
            # matching the in-game "sell all junk". This is what the "Select junk"
            # button bulk-checks (greys are worthless regardless of binding).
            "junk": sellable and quality == ITEM_QUALITY_POOR,
            "total": total,
            "sellable": sellable,
            "reason": reason,
            # Underlying stacks (one auction each); only meaningful when sellable.
            "guids": sorted(agg_guids[(region, entry, random_id, sb)]),
        })
        if total is not None:
            totals[region] += total

    for lst in sections.values():
        # Sellable first (highest total), then the rest by name.
        lst.sort(key=lambda x: (x["total"] is None, -(x["total"] or 0), x["name"].lower()))
    return sections, totals


# ---------------------------------------------------------------------------
# Auction listing helpers
# ---------------------------------------------------------------------------
def _sellable_by_guid(char_guid: int) -> dict[int, dict]:
    """Index the character's current sellable items by item-instance guid, so a
    posted selection can be re-validated against live inventory."""
    sections, _ = build_ah_view(db.character_inventory_breakdown(char_guid))
    by_guid: dict[int, dict] = {}
    for region in ("inventory", "bank"):
        for it in sections[region]:
            if it["sellable"]:
                for g in it["guids"]:
                    by_guid[g] = it
    return by_guid


def _posted_guids(values) -> set[int]:
    """Parse checkbox values (each a CSV of item-instance guids) into a guid set."""
    out: set[int] = set()
    for raw in values:
        for part in str(raw).split(","):
            if part.isdigit():
                out.add(int(part))
    return out


def _to_gsc(copper) -> tuple[int, int, int]:
    copper = max(0, int(copper or 0))
    return copper // 10000, (copper // 100) % 100, copper % 100


def _flash_listing_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_* command output into a user flash."""
    if not ok:
        flash("Couldn't reach the game server to list your items. Try again "
              "shortly.", "error")
        return
    listed = failed = deposit = 0
    err = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_RESULT"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "listed" and v.isdigit():
                    listed = int(v)
                elif k == "failed" and v.isdigit():
                    failed = int(v)
                elif k == "deposit" and v.isdigit():
                    deposit = int(v)
        elif line.startswith("GUILDHALL_ERROR"):
            err = line[len("GUILDHALL_ERROR"):].strip()

    if err:
        msgs = {
            "character-online": "Log out of the game before listing items.",
            "no-such-character": "That character could not be found.",
        }
        # insufficient-funds need=.. have=..
        if err.startswith("insufficient-funds"):
            flash("Not enough gold to cover the auction deposit.", "error")
        else:
            flash(msgs.get(err, f"Listing failed: {err}"), "error")
        return
    if listed:
        gold = deposit // 10000
        msg = f"Posted {listed} auction{'s' if listed != 1 else ''}"
        if deposit:
            msg += f" (deposit {gold}g {deposit % 10000 // 100}s {deposit % 100}c)"
        if failed:
            msg += f"; {failed} item{'s' if failed != 1 else ''} could not be listed"
        flash(msg + ".", "ok" if not failed else "error")
    else:
        flash("Nothing could be listed (items may have moved, sold, or the stack "
              "size was invalid).", "error")


# Per-item rejection reasons the vendor command emits (GUILDHALL_FAIL reason=..),
# mapped to a player-facing explanation so a failed instant-sale says WHY.
_VENDOR_FAIL_REASONS = {
    "not-held": "the game server no longer has them (they may have moved or sold "
                "in-game since the page loaded)",
    "not-sellable": "they can't be sold this way (an item already in an auction, a "
                    "non-empty bag, or a time-limited item)",
    "no-vendor-price": "a vendor won't buy them",
}


def _flash_vendor_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_VENDORED output (the instant-sell path)
    into a user flash, surfacing the per-item rejection reason when items fail."""
    if not ok:
        flash("Couldn't reach the game server to sell your items. Try again "
              "shortly.", "error")
        return
    sold = failed = gained = 0
    saw_result = False
    reasons: list[str] = []
    err = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_VENDORED"):
            saw_result = True
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "sold" and v.isdigit():
                    sold = int(v)
                elif k == "failed" and v.isdigit():
                    failed = int(v)
                elif k == "gained" and v.isdigit():
                    gained = int(v)
        elif line.startswith("GUILDHALL_FAIL"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "reason":
                    reasons.append(v)
        elif line.startswith("GUILDHALL_ERROR"):
            err = line[len("GUILDHALL_ERROR"):].strip()

    if err:
        msgs = {"no-such-character": "That character could not be found."}
        flash(msgs.get(err, f"Instant sale failed: {err}"), "error")
        return
    if not saw_result:
        # No result line at all: the worldserver almost certainly doesn't have the
        # vendor command yet (it needs a rebuild + restart).
        flash("The instant-sell command isn't available on the game server yet "
              "-- it may need updating (rebuild + restart).", "error")
        return

    def _why() -> str:
        if not reasons:
            return "items may have moved or changed"
        top = max(set(reasons), key=reasons.count)
        return _VENDOR_FAIL_REASONS.get(top, top)

    if sold:
        g, s, c = gained // 10000, gained % 10000 // 100, gained % 100
        msg = (f"Instantly sold {sold} item{'s' if sold != 1 else ''} to the "
               f"vendor for {g}g {s}s {c}c")
        if failed:
            msg += f"; {failed} could not be sold ({_why()})"
        flash(msg + ".", "ok" if not failed else "error")
    elif failed:
        flash(f"Couldn't sell {failed} item{'s' if failed != 1 else ''} to the "
              f"vendor: {_why()}.", "error")
    else:
        flash("Nothing was sold to the vendor.", "error")


def _flash_refresh_result(ok: bool, out: str) -> None:
    """Turn the worldserver's GUILDHALL_SAVED output (the force-save behind the
    "Refresh inventory" button) into a user flash."""
    if not ok:
        flash("Couldn't reach the game server to refresh. Try again shortly.",
              "error")
        return
    online = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("GUILDHALL_SAVED"):
            for tok in line.split()[1:]:
                k, _, v = tok.partition("=")
                if k == "online" and v.isdigit():
                    online = int(v)
        elif line.startswith("GUILDHALL_ERROR"):
            flash("Couldn't refresh your inventory. Try again shortly.", "error")
            return
    if online:
        flash("Inventory refreshed from your online character.", "ok")
    elif online == 0:
        flash("Your character is offline, so the list is already current.", "ok")
    else:
        flash("Couldn't refresh your inventory. Try again shortly.", "error")


if __name__ == "__main__":
    application = create_app()
    cfg = load_config().get("app", {})
    application.run(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 5000),
        debug=False,
    )
