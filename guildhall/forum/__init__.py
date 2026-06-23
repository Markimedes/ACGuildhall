"""Forum blueprint: the guild-scoped mini-forum. Official feed = guild leader
only; player feed = any member, with replies. Mounted at /forum.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from data import db
from guildhall.core import current_guild

bp = Blueprint("forum", __name__)

MAX_TITLE_LEN = 128
MAX_BODY_LEN = 2000
VALID_FEEDS = ("official", "player")


def _can_moderate(guild: dict, author_guid: int) -> bool:
    """A post/reply may be removed by its author or by the guild leader."""
    return guild["is_leader"] or author_guid == guild["char_guid"]


@bp.route("")
@login_required
def index():
    guild = current_guild()
    counts = db.feed_counts(guild["guildid"]) if guild else {}
    return render_template("forum_index.html", guild=guild, counts=counts)


@bp.route("/<feed>", methods=["GET", "POST"])
@login_required
def feed(feed):
    if feed not in VALID_FEEDS:
        abort(404)
    guild = current_guild()
    if not guild:
        flash("Join a guild in-game to use the forum.", "error")
        return redirect(url_for("forum.index"))

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
            return redirect(url_for("forum.feed", feed=feed))

    posts = db.list_feed(guild["guildid"], feed)
    can_post = feed == "player" or guild["is_leader"]
    return render_template(
        "forum_feed.html", feed=feed, posts=posts,
        guild=guild, can_post=can_post,
    )


@bp.route("/post/<int:post_id>")
@login_required
def post(post_id):
    guild = current_guild()
    post = db.get_post(post_id)
    if not post or not guild or post["guildid"] != guild["guildid"]:
        abort(404)  # not yours to see -> indistinguishable from missing
    replies = db.list_replies(post_id)
    return render_template(
        "forum_post.html", post=post, replies=replies, guild=guild
    )


@bp.route("/post/<int:post_id>/reply", methods=["POST"])
@login_required
def reply(post_id):
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
    return redirect(url_for("forum.post", post_id=post_id))


@bp.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def post_delete(post_id):
    guild = current_guild()
    post = db.get_post(post_id)
    if not post or not guild or post["guildid"] != guild["guildid"]:
        abort(404)
    if not _can_moderate(guild, post["author_guid"]):
        abort(403)
    db.delete_post(post_id)
    flash("Post deleted.", "ok")
    return redirect(url_for("forum.feed", feed=post["feed"]))


@bp.route("/reply/<int:reply_id>/delete", methods=["POST"])
@login_required
def reply_delete(reply_id):
    guild = current_guild()
    reply = db.get_reply(reply_id)
    if not reply or not guild or reply["guildid"] != guild["guildid"]:
        abort(404)
    if not _can_moderate(guild, reply["author_guid"]):
        abort(403)
    db.delete_reply(reply_id)
    flash("Reply deleted.", "ok")
    return redirect(url_for("forum.post", post_id=reply["post_id"]))
