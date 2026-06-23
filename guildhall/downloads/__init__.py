"""Downloads blueprint: lists the shared downloads dir and hands a single file
to nginx via X-Accel-Redirect (with a dev/small-file Flask fallback). The bare
filename is path-traversal guarded before any handoff.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    render_template,
    send_file,
)
from flask_login import login_required

bp = Blueprint("downloads", __name__)


def _human_size(num: int) -> str:
    """Render a byte count as a short human-readable string (e.g. '24.5 GB')."""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def list_downloads(dirpath: str) -> list[dict]:
    """Regular, non-hidden files directly inside ``dirpath`` (no recursion),
    sorted by name. Returns dicts with name, size, human size and mtime."""
    base = Path(dirpath)
    out: list[dict] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_file():
                continue
            st = entry.stat()
        except OSError:
            continue
        out.append({
            "name": entry.name,
            "size": st.st_size,
            "size_human": _human_size(st.st_size),
            "modified": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
        })
    return out


def resolve_download(dirpath: str, name: str) -> Path | None:
    """Resolve ``name`` to a real file directly inside ``dirpath``, or None.

    Rejects any path separators / traversal: the requested name must be a bare
    filename whose resolved path stays inside the (resolved) downloads dir.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    base = Path(dirpath).resolve()
    try:
        target = (base / name).resolve()
        if target.parent != base or not target.is_file():
            return None
    except OSError:
        return None
    return target


@bp.route("")
@login_required
def index():
    files = list_downloads(current_app.config["DOWNLOADS_DIR"])
    return render_template("downloads.html", files=files)


@bp.route("/<name>")
@login_required
def file(name):
    target = resolve_download(current_app.config["DOWNLOADS_DIR"], name)
    if target is None:
        abort(404)

    prefix = current_app.config["DOWNLOADS_INTERNAL_PREFIX"]
    if prefix:
        # Hand the byte-pushing to nginx (sendfile + range/resume support):
        # Flask only authorized the request. The internal location must map
        # the same DOWNLOADS_DIR. Encode the name so spaces/specials survive.
        resp = Response()
        resp.headers["X-Accel-Redirect"] = prefix.rstrip("/") + "/" + quote(target.name)
        resp.headers["Content-Type"] = "application/octet-stream"
        resp.headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(target.name)}"
        )
        return resp
    # Dev / small-file fallback: stream through Flask (supports Range too).
    return send_file(
        target, as_attachment=True, download_name=target.name,
        conditional=True,
    )
