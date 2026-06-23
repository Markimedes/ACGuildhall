"""Gunicorn entrypoint.

    gunicorn ... wsgi:app

Builds the application from the production config (read from GUILDHALL_* env
vars). For the dev server, run:  python wsgi.py
"""

from __future__ import annotations

from guildhall import create_app

app = create_app()


if __name__ == "__main__":
    app.run(
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=False,
    )
