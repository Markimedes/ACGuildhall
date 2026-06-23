"""Invites blueprint: authenticated invite-token management plus the PUBLIC,
unauthenticated invite redemption (account self-registration). Unprefixed and
with explicit paths so the existing URLs -- in particular the /invite/<token>
links already shared with players -- are preserved exactly.
"""

from __future__ import annotations

import hashlib
import re
import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from data import db, srp6
from guildhall.extensions import limiter

bp = Blueprint("invites", __name__)

MAX_PASS_LEN = 16  # mirrors AccountMgr MAX_PASS_STR
MAX_EMAIL_LEN = 255  # mirrors AccountMgr MAX_EMAIL_STR
# Account names: letters/digits/_-. ; no ':' (SRP6 delimiter) or whitespace.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,17}$")


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _render_invites(new_link=None):
    account_id = current_user.account_id
    return render_template(
        "invites.html",
        tokens=db.invite_available_tokens(account_id, current_app.config["INVITE_TOKENS_DEFAULT"]),
        invites=db.invite_list_for(account_id),
        ttl_hours=current_app.config["INVITE_TTL_HOURS"],
        new_link=new_link,
    )


@bp.route("/invites", methods=["GET", "POST"])
@login_required
def index():
    account_id = current_user.account_id
    if request.method == "POST":
        tokens = db.invite_available_tokens(account_id, current_app.config["INVITE_TOKENS_DEFAULT"])
        if tokens["available"] <= 0:
            flash("You have no invite tokens available.", "error")
            return redirect(url_for("invites.index"))
        token = secrets.token_urlsafe(32)
        db.invite_create(account_id, _token_hash(token), current_app.config["INVITE_TTL_HOURS"])
        # Prefer an explicit public base URL (correct behind nginx / with a port);
        # fall back to the request host if it isn't configured.
        base = current_app.config["PUBLIC_BASE_URL"]
        path = url_for("invites.redeem", token=token)
        link = (base + path) if base else url_for(
            "invites.redeem", token=token, _external=True
        )
        flash("Invite created — copy the link now; it is shown only once.", "ok")
        return _render_invites(new_link=link)
    return _render_invites()


@bp.route("/invites/<int:invite_id>/revoke", methods=["POST"])
@login_required
def revoke(invite_id):
    if db.invite_revoke(invite_id, current_user.account_id):
        flash("Invite canceled; token refunded.", "ok")
    return redirect(url_for("invites.index"))


# --- invite redemption (PUBLIC, unauthenticated) -------------------------
@bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("10 per 10 minutes", methods=["POST"])
def redeem(token):
    token_hash = _token_hash(token)
    if request.method == "POST":
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
                current_app.config["NEW_ACCOUNT_EXPANSION"], e,
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
