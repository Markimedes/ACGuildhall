"""Pure-Python port of AzerothCore's SRP6 registration/verification.

Mirrors src/common/Cryptography/Authentication/SRP6.cpp. The account table stores
``salt binary(32)`` and ``verifier binary(32)`` -- there is no password hash.

Endianness is the part people get wrong. AzerothCore's BigNumber treats byte
arrays as little-endian, so:

  * the SHA1 digest fed into the modular exponent is read little-endian, and
  * the resulting verifier is serialised little-endian into 32 bytes.

Both the username and the password are upper-cased (Latin only) before hashing,
exactly as AccountMgr does via Utf8ToUpperOnlyLatin.
"""

from __future__ import annotations

import hashlib
import os

# g = 7, N = the 256-bit safe prime AzerothCore uses (see SRP6.cpp).
_G = 7
_N = int(
    "894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16
)

SALT_LENGTH = 32
VERIFIER_LENGTH = 32


def _normalize(value: str) -> str:
    """Upper-case ASCII/Latin letters, matching Utf8ToUpperOnlyLatin closely
    enough for the account names/passwords this panel accepts."""
    return value.upper()


def calculate_verifier(username: str, password: str, salt: bytes) -> bytes:
    """v = g ^ H(s || H(UPPER(u) ':' UPPER(p))) mod N, serialised little-endian."""
    if len(salt) != SALT_LENGTH:
        raise ValueError(f"salt must be {SALT_LENGTH} bytes, got {len(salt)}")

    user = _normalize(username)
    pwd = _normalize(password)

    inner = hashlib.sha1(f"{user}:{pwd}".encode("utf-8")).digest()
    x_digest = hashlib.sha1(salt + inner).digest()
    x = int.from_bytes(x_digest, "little")

    verifier = pow(_G, x, _N)
    return verifier.to_bytes(VERIFIER_LENGTH, "little")


def make_registration_data(username: str, password: str) -> tuple[bytes, bytes]:
    """Return ``(salt, verifier)`` for a new password. Salt is 32 random bytes."""
    salt = os.urandom(SALT_LENGTH)
    return salt, calculate_verifier(username, password, salt)


def check_login(
    username: str, password: str, salt: bytes, verifier: bytes
) -> bool:
    """Constant-time check that ``password`` matches the stored salt/verifier."""
    import hmac

    candidate = calculate_verifier(username, password, salt)
    return hmac.compare_digest(candidate, bytes(verifier))
