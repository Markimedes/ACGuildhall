"""Application error handlers.

Replaces Werkzeug's unstyled default pages with the app's own ``error.html``
(which extends ``base.html``, so error pages carry the same shell and nav). The
HTTP status is preserved -- only the rendered body changes -- so callers that
``abort(404)`` / ``abort(403)`` and the rate limiter's 429 still report the
right code.
"""

from __future__ import annotations

from flask import Flask, render_template

# Friendly, non-leaky copy per status. Anything not listed falls through to
# Werkzeug's default (e.g. 405), which is fine -- these are the ones the app
# actually raises.
_MESSAGES = {
    400: "That request couldn't be processed.",
    403: "You don't have access to that.",
    404: "That page doesn't exist.",
    429: "Too many requests -- please slow down and try again shortly.",
    500: "Something went wrong on our end.",
}


def register_error_handlers(app: Flask) -> None:
    def handler(code: int, message: str):
        # Bind code/message per registration; the exception itself is unused.
        def render(_exc):
            return render_template("error.html", code=code, message=message), code

        return render

    for code, message in _MESSAGES.items():
        app.register_error_handler(code, handler(code, message))
