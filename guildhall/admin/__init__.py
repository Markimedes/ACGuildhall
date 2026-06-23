"""Admin blueprint: invite-token allowance overrides (admin-gated). Mounted at
/admin.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from data import db
from guildhall.auth import admin_required

bp = Blueprint("admin", __name__)


@bp.route("/tokens", methods=["GET", "POST"])
@admin_required
def tokens():
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
        return redirect(url_for("admin.tokens"))
    return render_template(
        "admin_tokens.html",
        overrides=db.allowance_list(),
        default_tokens=current_app.config["INVITE_TOKENS_DEFAULT"],
    )
