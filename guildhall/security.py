"""Security headers (CSP et al.). Relocated verbatim from the old app factory;
the policy is unchanged. Registered on the app by ``create_app``.
"""

from __future__ import annotations

from flask import Flask

# Wowhead item tooltips need their external widget script + data host, and the
# widget injects inline styles -- hence the loosened script/style/connect-src.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://wow.zamimg.com https://nether.wowhead.com https://www.wowhead.com; "
    "style-src 'self' 'unsafe-inline' https://wow.zamimg.com; "
    "img-src 'self' data: https://wow.zamimg.com; "
    "connect-src 'self' https://nether.wowhead.com https://www.wowhead.com; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def register_security(app: Flask) -> None:
    @app.after_request
    def set_headers(resp):
        resp.headers["Content-Security-Policy"] = _CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp
