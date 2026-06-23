"""Characterization tests for the auth surface: login_required gating, the
hand-rolled CSRF check, SRP6 login success/failure, and the per-process login
rate limit. These pin down today's behavior so Phase 2 (Flask-Login /
Flask-WTF / Flask-Limiter) can prove it didn't regress.
"""

from __future__ import annotations

from data import db
from data import srp6


def test_root_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_post_without_csrf_is_rejected(client):
    resp = client.post("/login", data={"username": "x", "password": "y"})
    assert resp.status_code == 400


def _account(username="TESTER", password="secret"):
    salt, verifier = srp6.make_registration_data(username, password)
    return {"id": 1, "username": username, "salt": salt, "verifier": verifier}


def test_login_success_sets_session(client, csrf_token, monkeypatch):
    acct = _account()
    monkeypatch.setattr(db, "get_account_by_username", lambda u: acct)
    resp = client.post("/login", data={
        "username": "TESTER", "password": "secret", "csrf_token": csrf_token,
    })
    assert resp.status_code == 302
    # Flask-Login records the identity under "_user_id" (User.get_id()).
    with client.session_transaction() as sess:
        assert sess["_user_id"] == "1"


def test_login_wrong_password_rerenders(client, csrf_token, monkeypatch):
    acct = _account()
    monkeypatch.setattr(db, "get_account_by_username", lambda u: acct)
    resp = client.post("/login", data={
        "username": "TESTER", "password": "WRONG", "csrf_token": csrf_token,
    })
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_login_rate_limited_after_ten_attempts(client, csrf_token, monkeypatch):
    # Always-invalid account so every attempt fails the credential check but
    # still counts against the rate bucket.
    monkeypatch.setattr(db, "get_account_by_username", lambda u: None)
    data = {"username": "x", "password": "y", "csrf_token": csrf_token}
    for _ in range(10):
        assert client.post("/login", data=data).status_code == 200
    assert client.post("/login", data=data).status_code == 429
