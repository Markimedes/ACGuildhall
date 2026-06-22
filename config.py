"""Centralized configuration for Guildhall.

The single place the ``GUILDHALL_*`` environment is read. The Flask app loads a
``Config`` into ``app.config`` via ``from_object``; the service modules (``db``,
``soap``, ``ahservice``, ``exploits``) are handed their slice through their
``init``/``configure`` functions from the same object. The news scheduler and the
standalone CLI tools build a ``Config`` the same way.

Env is read at *instantiation* time, not import time, so importing any module is
side-effect-free and the app can be constructed with different config for tests
(see ``TestingConfig``).
"""

from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val not in (None, "") else default


def _float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val not in (None, "") else default


class Config:
    """Base configuration, built from the environment when instantiated.

    Only UPPER_CASE attributes are copied into ``app.config`` by Flask's
    ``from_object``; the nested service dicts (``DATABASE``/``SOAP``/...) are
    UPPER_CASE too, so they ride along harmlessly and are also passed explicitly
    to each service module's configure step.
    """

    # Static Flask cookie hardening (not env-driven).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    TESTING = False
    DEBUG = False

    def __init__(self) -> None:
        env = os.environ

        # --- Flask / session ---
        self.SECRET_KEY = env.get("GUILDHALL_SECRET_KEY")
        self.BEHIND_TLS = _bool("GUILDHALL_BEHIND_TLS", False)
        # Secure cookie only makes sense once TLS terminates in front of us.
        self.SESSION_COOKIE_SECURE = self.BEHIND_TLS
        self.TRUST_PROXY = _bool("GUILDHALL_TRUST_PROXY", False)
        # Cap request bodies. The auction-list JSON payload is the only large
        # input; everything else is a small form post.
        self.MAX_CONTENT_LENGTH = _int("GUILDHALL_MAX_CONTENT_LENGTH", 512 * 1024)

        # --- dev-server bind (used only by `python app.py`) ---
        self.HOST = env.get("GUILDHALL_HOST", "127.0.0.1")
        self.PORT = _int("GUILDHALL_PORT", 5000)

        # --- invites / accounts ---
        self.INVITE_TOKENS_DEFAULT = _int("GUILDHALL_INVITE_TOKENS_DEFAULT", 3)
        self.INVITE_TTL_HOURS = _int("GUILDHALL_INVITE_TTL_HOURS", 12)
        self.NEW_ACCOUNT_EXPANSION = _int("GUILDHALL_NEW_ACCOUNT_EXPANSION", 2)
        self.ADMIN_GMLEVEL = _int("GUILDHALL_ADMIN_GMLEVEL", 3)
        self.PUBLIC_BASE_URL = (env.get("GUILDHALL_PUBLIC_BASE_URL") or "").rstrip("/")

        # --- roster demand cache TTL ---
        self.DEMAND_REFRESH_MINUTES = _int("GUILDHALL_DEMAND_REFRESH_MINUTES", 360)

        # --- auction deposit preview / refresh (authoritative deposit is
        # computed server-side; these only drive the preview) ---
        self.AH_DEPOSIT_PERCENT = _float("GUILDHALL_AH_DEPOSIT_PERCENT", 5.0)
        self.AH_DEPOSIT_RATE = _float("GUILDHALL_AH_DEPOSIT_RATE", 1.0)
        self.AH_REFRESH_SECONDS = _int("GUILDHALL_AH_REFRESH_SECONDS", 60)

        # --- downloads ---
        self.DOWNLOADS_DIR = env.get("GUILDHALL_DOWNLOADS_DIR", "/media/plex/downloads")
        self.DOWNLOADS_INTERNAL_PREFIX = env.get("GUILDHALL_DOWNLOADS_INTERNAL_PREFIX") or ""

        # --- service-module slices (handed to their configure()/init) ---
        self.DATABASE = {
            "host": env.get("GUILDHALL_DB_HOST"),
            "port": _int("GUILDHALL_DB_PORT", 3306),
            "user": env.get("GUILDHALL_DB_USER"),
            "password": env.get("GUILDHALL_DB_PASSWORD"),
            "pool_size": _int("GUILDHALL_DB_POOL_SIZE", 5),
        }
        self.SOAP = {
            "url": (env.get("GUILDHALL_SOAP_URL") or "").strip(),
            "user": env.get("GUILDHALL_SOAP_USER", ""),
            "password": env.get("GUILDHALL_SOAP_PASS", ""),
            "timeout": _float("GUILDHALL_SOAP_TIMEOUT", 5.0),
        }
        self.AHPRICING = {
            "url": env.get("GUILDHALL_AHPRICING_URL",
                           "http://ahpricingservice:8089/price"),
            "timeout": _float("GUILDHALL_AHPRICING_TIMEOUT", 1.5),
        }
        self.EXPLOITS = {
            "exploits_max": _int("GUILDHALL_EXPLOITS_MAX", 4),
            "obituaries_max": _int("GUILDHALL_OBITUARIES_MAX", 3),
            "milestone_points": _int("GUILDHALL_EXPLOITS_MILESTONE_POINTS", 20),
            "max_level": _int("GUILDHALL_MAX_LEVEL", 80),
            "witness_radius": _float("GUILDHALL_WITNESS_RADIUS", 35.0),
        }

    def validate(self) -> "Config":
        """Fail fast on missing required secret / DB credentials."""
        if not self.SECRET_KEY:
            raise RuntimeError("GUILDHALL_SECRET_KEY is required")
        for key in ("host", "user", "password"):
            if not self.DATABASE.get(key):
                raise RuntimeError(f"GUILDHALL_DB_{key.upper()} is required")
        return self


class DevelopmentConfig(Config):
    def __init__(self) -> None:
        super().__init__()
        self.DEBUG = True


class TestingConfig(Config):
    def __init__(self) -> None:
        super().__init__()
        self.TESTING = True


class ProductionConfig(Config):
    pass
