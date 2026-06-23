"""Characterization tests for forum posting: a guilded member can post to the
player feed, and the title/body length limits reject oversized input before any
DB write. Pins the manual validation that Phase 2 moves into a Flask-WTF form.
The guild/character context and the DB writes are faked.
"""

from __future__ import annotations

import db

# A single guilded character for the logged-in account. guid != leaderguid, so
# the account is a normal member (can post to the player feed, not 'official').
_CHAR = {
    "guid": 10, "name": "Memberchar", "level": 80,
    "guildid": 5, "guild_name": "Testers", "leaderguid": 99,
    "online": 0, "money": 0,
}


def _setup(monkeypatch, create_spy):
    monkeypatch.setattr(db, "account_characters", lambda aid: [_CHAR])
    monkeypatch.setattr(db, "is_admin", lambda aid, lvl: False)
    monkeypatch.setattr(db, "list_feed", lambda gid, feed: [])
    monkeypatch.setattr(db, "create_post", create_spy)
    # The Flask-Login user_loader resolves the logged-in account by id.
    monkeypatch.setattr(db, "get_account_by_id",
                        lambda aid: {"id": 1, "username": "MEMBER",
                                     "salt": b"", "verifier": b""})


def _login(client):
    # Flask-Login keys the session on "_user_id" (the User.get_id() string).
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"


def test_player_post_succeeds(client, csrf_token, monkeypatch):
    calls = []
    _setup(monkeypatch, lambda *a: calls.append(a))
    _login(client)
    resp = client.post("/forum/player", data={
        "title": "Hello", "body": "world", "csrf_token": csrf_token,
    })
    assert resp.status_code == 302
    assert len(calls) == 1
    guildid, feed, author_guid, title, body = calls[0]
    assert (guildid, feed, author_guid, title, body) == (5, "player", 10, "Hello", "world")


def test_post_too_long_is_rejected(client, csrf_token, monkeypatch):
    calls = []
    _setup(monkeypatch, lambda *a: calls.append(a))
    _login(client)
    resp = client.post("/forum/player", data={
        "title": "ok", "body": "y" * 2001, "csrf_token": csrf_token,
    })
    assert resp.status_code == 200
    assert calls == []  # rejected before the DB write


def test_empty_post_is_rejected(client, csrf_token, monkeypatch):
    calls = []
    _setup(monkeypatch, lambda *a: calls.append(a))
    _login(client)
    resp = client.post("/forum/player", data={
        "title": "", "body": "", "csrf_token": csrf_token,
    })
    assert resp.status_code == 200
    assert calls == []
