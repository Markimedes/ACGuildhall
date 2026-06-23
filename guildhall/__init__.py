"""Guildhall -- player web panel for an AzerothCore realm.

Application factory. The route bodies live in per-area blueprints under this
package; the data-access and service layer lives in the top-level ``data``
package; offline tooling lives in ``tools``. Configuration is read once into a
``Config`` instance (see ``config.py``) and handed to the service modules.

Entrypoint: ``wsgi.py`` builds ``app = create_app()`` for gunicorn.
"""

from __future__ import annotations

from flask import Flask

from config import Config, ProductionConfig
from data import ahservice, db, soap

from .extensions import csrf, init_news_desk, limiter, login_manager
from .security import register_security


def create_app(config: Config | None = None) -> Flask:
    cfg = config if config is not None else ProductionConfig()
    cfg.validate()

    db.init_pool(cfg.DATABASE)
    soap.configure(cfg.SOAP)
    ahservice.configure(cfg.AHPRICING)

    # Templates/static stay flat at the repo root (one level above this package).
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(cfg)

    # Behind nginx: trust one proxy hop so request.remote_addr (rate limiting)
    # and the URL scheme/Secure cookie reflect the real client, not the proxy.
    if cfg.TRUST_PROXY:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Standard-library plumbing (Phase 2): CSRF, auth, rate limiting.
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    # AI news desk (configured from GUILDHALL_GEMINI_* env vars). If no key /
    # SDK, it stays dark and the News tab shows an offline notice. Lives on
    # app.extensions, not app.config.
    init_news_desk(app)

    register_security(app)

    # Blueprints. core is unprefixed (dashboard, chronicle, nav context + jinja
    # filters); auth and invites stay unprefixed so existing URLs (notably the
    # public /invite/<token> redemption links in the wild) are preserved.
    from .admin import bp as admin_bp
    from .auction import bp as auction_bp
    from .auth import bp as auth_bp
    from .core import bp as core_bp
    from .downloads import bp as downloads_bp
    from .forum import bp as forum_bp
    from .invites import bp as invites_bp
    from .news import bp as news_bp
    from .roster import bp as roster_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(forum_bp, url_prefix="/forum")
    app.register_blueprint(roster_bp, url_prefix="/roster")
    app.register_blueprint(auction_bp, url_prefix="/auctionhouse")
    app.register_blueprint(news_bp, url_prefix="/news")
    app.register_blueprint(invites_bp)
    app.register_blueprint(downloads_bp, url_prefix="/downloads")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    return app
