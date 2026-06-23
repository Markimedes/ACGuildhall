"""Characterization tests for public invite redemption -- the unauthenticated
account-creation path. Pins the input validation (USERNAME_RE, password
length/match) and upper-case normalization that Phase 2 will move into a
Flask-WTF form, plus the 'taken' branch. The DB write itself is faked.
"""

from __future__ import annotations

from data import db


def _post(client, csrf, **overrides):
    data = {
        "username": "Tester", "password": "secret",
        "confirm_password": "secret", "email": "a@b.com",
        "csrf_token": csrf,
    }
    data.update(overrides)
    return client.post("/invite/sometoken", data=data)


def test_invite_redeem_creates_account_normalized(client, csrf_token, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "redeem_invite_and_create_account",
                        lambda *a, **k: calls.append(a))
    resp = _post(client, csrf_token)
    assert resp.status_code == 200
    assert b"Account created" in resp.data
    assert len(calls) == 1
    # AccountMgr-style upper-casing of username and email.
    args = calls[0]
    assert args[1] == "TESTER"      # username (positional after token_hash)
    assert args[5] == "A@B.COM"     # email


def test_invite_redeem_rejects_bad_username(client, csrf_token, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "redeem_invite_and_create_account",
                        lambda *a, **k: calls.append(a))
    resp = _post(client, csrf_token, username="bad name!")
    assert resp.status_code == 200
    assert calls == []  # validation blocked it before any DB write


def test_invite_redeem_rejects_password_mismatch(client, csrf_token, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "redeem_invite_and_create_account",
                        lambda *a, **k: calls.append(a))
    resp = _post(client, csrf_token, confirm_password="different")
    assert resp.status_code == 200
    assert calls == []


def test_invite_redeem_taken_username(client, csrf_token, monkeypatch):
    def boom(*a, **k):
        raise db.InviteError("taken")
    monkeypatch.setattr(db, "redeem_invite_and_create_account", boom)
    resp = _post(client, csrf_token)
    assert resp.status_code == 200
    assert b"already taken" in resp.data
