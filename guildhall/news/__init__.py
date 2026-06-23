"""News blueprint: today's AI-generated market newspaper and the read-only
archive of past editions. The cache-first market generation (``todays_news``)
lives in ``data.news_ai`` so the offline scheduler can share it; the read-only
column split and edition navigation are here. Mounted at /news.
"""

from __future__ import annotations

from datetime import date

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    url_for,
)
from flask_login import login_required

from data import ahservice, db, news_prompts
from data.news_ai import todays_news

bp = Blueprint("news", __name__)


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


@bp.route("")
@login_required
def index():
    desk = current_app.extensions["news_desk"]
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


@bp.route("/<edition>")
@login_required
def archive(edition):
    """A past day's edition, read-only -- whatever was cached for that date.
    Today (or anything not yet past) redirects to the live page, which can
    still generate missing stories."""
    try:
        edition = date.fromisoformat(edition).isoformat()
    except ValueError:
        abort(404)
    if edition >= date.today().isoformat():
        return redirect(url_for("news.index"))

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
