"""Auth blueprint: login / logout, the self-service password change (SRP6), and
character selection. Also exposes ``admin_required`` (used by the admin
blueprint).
"""

from __future__ import annotations

import functools

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter.util import get_remote_address
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from data import db, srp6
from guildhall.core import current_characters
from guildhall.extensions import User, limiter

bp = Blueprint("auth", __name__)

MAX_PASS_LEN = 16  # mirrors AccountMgr MAX_PASS_STR


def admin_required(view):
    """Like Flask-Login's ``login_required`` but also requires admin gmlevel.
    Unauthenticated -> the login redirect; authenticated non-admin -> 403."""
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 5 minutes", methods=["POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        account = db.get_account_by_username(username)
        if account and srp6.check_login(
            account["username"], password,
            account["salt"], account["verifier"],
        ):
            session.clear()  # guard against session fixation
            login_user(User(account["id"], account["username"]))
            dest = request.args.get("next", "")
            if not dest.startswith("/"):
                dest = url_for("core.dashboard")
            return redirect(dest)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/character/select", methods=["POST"])
@login_required
def select_character():
    guid = request.form.get("guid", type=int)
    if guid and any(ch["guid"] == guid for ch in current_characters()):
        session["char_guid"] = guid
    dest = request.referrer
    if not dest or not dest.startswith(request.host_url):
        dest = url_for("core.dashboard")
    return redirect(dest)


@bp.route("/password", methods=["GET", "POST"])
@login_required
@limiter.limit(
    "5 per 5 minutes",
    key_func=lambda: f"pw:{current_user.account_id}:{get_remote_address()}",
    methods=["POST"],
)
def password():
    if request.method == "POST":
        account_id = current_user.account_id  # identity from session only
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
            return redirect(url_for("auth.password"))
    return render_template("password.html")
