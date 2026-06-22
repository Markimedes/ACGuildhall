"""Shared test fixtures.

These build a real ``create_app()`` instance but never touch MySQL or any
external service: ``db.init_pool`` is stubbed to a no-op and individual tests
monkeypatch the specific ``db`` functions they exercise. This is the cheap
unit-test boundary the refactor plan calls for (the data layer is already a thin
module of standalone functions).
"""

from __future__ import annotations

import pytest

import app as app_module
import db
from config import TestingConfig

# Minimal env required by Config.validate(); the DB creds are inert because
# init_pool is stubbed below.
_REQUIRED_ENV = {
    "GUILDHALL_SECRET_KEY": "test-secret-key",
    "GUILDHALL_DB_HOST": "localhost",
    "GUILDHALL_DB_USER": "test",
    "GUILDHALL_DB_PASSWORD": "test",
}


@pytest.fixture
def app(monkeypatch):
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    # Never open a real connection pool during tests.
    monkeypatch.setattr(db, "init_pool", lambda cfg: None)
    # TestingConfig reads the env we just set; pass it explicitly so we exercise
    # the create_app(config) path.
    return app_module.create_app(TestingConfig())


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf_token(client):
    """Seed a CSRF token into the test session and return it, so POST tests can
    pass the hand-rolled CSRF check (Phase 2 will replace this with Flask-WTF)."""
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token
