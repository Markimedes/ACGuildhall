"""Flask extension singletons.

Instantiated here unbound and wired to the app in ``create_app`` via
``init_app``. Keeping the live extension objects in one module (rather than
stuffed into ``app.config``, where the hand-rolled CSRF token, the per-process
rate-limit dict, and the news desk used to live) is the standard Flask pattern
and keeps mutable runtime state out of config.
"""

from __future__ import annotations

from flask import current_app, redirect, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, UserMixin
from flask_wtf import CSRFProtect

import db
from news_ai import NewsDesk

# Flask-WTF CSRF. Replaces the hand-rolled token/compare in security.py; it
# registers the ``csrf_token()`` Jinja global the templates already call.
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "login"
# Don't flash Flask-Login's default "please log in" message; the bare redirect
# matches the prior hand-rolled behavior.
login_manager.login_message = None
# "basic", not "strong": "strong" wipes the session whenever the request
# identifier (incl. IP) changes, which logs out mobile / VPN users on every
# network hop (Flask-Login session-protection caveat, skill issue #5).
login_manager.session_protection = "basic"


class User(UserMixin):
    """Minimal Flask-Login identity over an ``acore_auth.account`` row.

    Identity is the account id. Admin status is resolved lazily -- one query,
    cached on the user object for the life of the request -- so ordinary pages
    that never check ``is_admin`` don't pay for it (mirrors the old
    ``g._is_admin`` caching).
    """

    def __init__(self, account_id: int, username: str):
        self.account_id = account_id
        self.username = username
        self._is_admin: bool | None = None

    def get_id(self) -> str:
        return str(self.account_id)

    @property
    def is_admin(self) -> bool:
        if self._is_admin is None:
            self._is_admin = db.is_admin(
                self.account_id, current_app.config["ADMIN_GMLEVEL"]
            )
        return self._is_admin


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    account = db.get_account_by_id(int(user_id))
    if not account:
        return None
    return User(account["id"], account["username"])


@login_manager.unauthorized_handler
def _unauthorized():
    # Preserve the prior redirect target exactly: /login?next=<path> (a clean
    # path, which the login view's ``startswith('/')`` guard then accepts).
    return redirect(url_for("login", next=request.path))


# Rate limiting. ``default_limits=[]`` means nothing is limited unless a route
# opts in with @limiter.limit(...). The storage backend is read from
# ``RATELIMIT_STORAGE_URI`` (memory:// by default -- see the multi-worker note in
# config.py); init_app builds a fresh store per app, so tests are isolated.
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def init_news_desk(app) -> None:
    """Build the AI news desk from the environment and stash it on the app (in
    ``app.extensions``, not ``app.config``). Routes read it via
    ``current_app.extensions['news_desk']``."""
    app.extensions["news_desk"] = NewsDesk.from_env()
