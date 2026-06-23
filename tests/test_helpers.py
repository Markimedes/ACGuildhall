"""Characterization tests for pure helper functions in app.py.

These lock in current behavior before the refactor moves the helpers into
blueprints; they must keep passing afterward.
"""

from __future__ import annotations

from guildhall.auction import _to_gsc
from guildhall.downloads import _human_size, resolve_download


def test_to_gsc_splits_copper():
    assert _to_gsc(0) == (0, 0, 0)
    assert _to_gsc(1) == (0, 0, 1)
    assert _to_gsc(101) == (0, 1, 1)
    assert _to_gsc(10101) == (1, 1, 1)


def test_to_gsc_clamps_negative_and_none():
    assert _to_gsc(-5) == (0, 0, 0)
    assert _to_gsc(None) == (0, 0, 0)


def test_human_size():
    assert _human_size(0) == "0 B"
    assert _human_size(512) == "512 B"
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1024 * 1024) == "1.0 MB"


def test_resolve_download_accepts_plain_file(tmp_path):
    f = tmp_path / "patch.zip"
    f.write_text("x")
    assert resolve_download(str(tmp_path), "patch.zip") == f.resolve()


def test_resolve_download_rejects_traversal_and_missing(tmp_path):
    (tmp_path / "patch.zip").write_text("x")
    for bad in (
        "",                  # empty
        ".",                 # current dir
        "..",                # parent
        "../patch.zip",      # escape attempt
        "sub/patch.zip",     # nested path
        "foo/../patch.zip",  # traversal that resolves back inside
        "/etc/passwd",       # absolute
        "missing.zip",       # not present
    ):
        assert resolve_download(str(tmp_path), bad) is None, bad
