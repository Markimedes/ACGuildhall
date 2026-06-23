"""Core blueprint: the dashboard + chronicle pages, the shared
character/guild context helpers used by every other blueprint, and the app-wide
nav context processor and Jinja filters.
"""

from __future__ import annotations

from flask import Blueprint, g, render_template, session
from flask_login import current_user, login_required
from markupsafe import Markup, escape

from data import db
from data.professions import profession_name

bp = Blueprint("core", __name__)


# ---------------------------------------------------------------------------
# Shared character / guild context (imported by the other blueprints)
# ---------------------------------------------------------------------------
def current_characters():
    """The logged-in account's characters (request-cached on flask.g)."""
    if not current_user.is_authenticated:
        return []
    if not hasattr(g, "_chars"):
        g._chars = db.account_characters(current_user.account_id)
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


# ---------------------------------------------------------------------------
# App-wide nav context + Jinja filters
# ---------------------------------------------------------------------------
@bp.app_context_processor
def inject_nav():
    return {
        "is_admin": current_user.is_authenticated and current_user.is_admin,
        "characters": current_characters(),
        "active_character": active_character(),
    }


@bp.app_template_filter("nl2br")
def nl2br(value: str) -> Markup:
    # Escape FIRST, then join escaped segments with a trusted <br>.
    return Markup("<br>").join(escape(value).split("\n"))


@bp.app_template_filter("profession")
def profession(skill_id: int) -> str:
    return profession_name(skill_id)


@bp.app_template_filter("icon_url")
def icon_url(name: str, size: str = "medium") -> str:
    # Wowhead icon CDN; sizes: small (18), medium (36), large (56).
    return f"https://wow.zamimg.com/images/wow/icons/{size}/{name or 'inv_misc_questionmark'}.jpg"


@bp.app_template_filter("money")
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
# Routes
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        username=current_user.username,
        guild=current_guild(),
    )


@bp.route("/chronicle")
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
