"""SOAP client for running worldserver console commands.

AzerothCore's worldserver exposes a SOAP endpoint (``SOAP.Enabled = 1`` in
worldserver.conf) that runs any console/GM command as if typed at the server
console. Authentication is HTTP Basic with a game **account** username/password;
the account must have ``SEC_ADMINISTRATOR`` (gmlevel 3) -- see
``src/server/apps/worldserver/ACSoap/ACSoap.cpp``. The command runs in the world
thread and the call blocks until it finishes.

This is intentionally generic (``command()`` runs anything) so other features can
reuse it; the auction tab uses it for ``reload auctions``.

Config (all GUILDHALL_SOAP_*):
  GUILDHALL_SOAP_URL       e.g. http://host.docker.internal:7878/ (empty = disabled)
  GUILDHALL_SOAP_USER      admin account username
  GUILDHALL_SOAP_PASS      that account's password (plaintext; checked against the
                           SRP verifier server-side)
  GUILDHALL_SOAP_TIMEOUT   per-call timeout in seconds (default 5)
"""

from __future__ import annotations

import base64
import os
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

URL = os.environ.get("GUILDHALL_SOAP_URL", "").strip()
USER = os.environ.get("GUILDHALL_SOAP_USER", "")
PASS = os.environ.get("GUILDHALL_SOAP_PASS", "")
TIMEOUT = float(os.environ.get("GUILDHALL_SOAP_TIMEOUT", "5"))

_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<SOAP-ENV:Envelope'
    ' xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:ns1="urn:AC">'
    "<SOAP-ENV:Body><ns1:executeCommand>"
    "<command>{cmd}</command>"
    "</ns1:executeCommand></SOAP-ENV:Body></SOAP-ENV:Envelope>"
)


def enabled() -> bool:
    """True when a SOAP endpoint and credentials are configured."""
    return bool(URL and USER and PASS)


def command(cmd: str) -> tuple[bool, str]:
    """Run a console command on the worldserver. Returns ``(ok, output)``.

    ``ok`` is False (with the error text) on any transport/auth/command failure;
    callers decide whether that is fatal. Never raises."""
    if not enabled():
        return False, "SOAP is not configured"

    body = _ENVELOPE.format(cmd=escape(cmd)).encode("utf-8")
    token = base64.b64encode(f"{USER}:{PASS}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "Authorization": f"Basic {token}",
            "SOAPAction": "urn:AC#executeCommand",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, _extract_result(raw)
    except urllib.error.HTTPError as e:  # 401/403/500 (incl. SOAP faults)
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        return False, f"HTTP {e.code}: {detail or e.reason}"
    except Exception as e:  # noqa: BLE001 -- unreachable, timeout, DNS, ...
        return False, str(e)


def _extract_result(xml_text: str) -> str:
    """Return the text content of the <result> element in the SOAP response.

    Falls back to the raw text if parsing fails so callers always get something."""
    try:
        root = ET.fromstring(xml_text)
        # <result> is inside Body > executeCommandResponse, any namespace.
        for elem in root.iter():
            if elem.tag.endswith("}result") or elem.tag == "result":
                return (elem.text or "").strip()
    except ET.ParseError:
        pass
    return xml_text


def reload_auctions() -> tuple[bool, str]:
    """Tell the worldserver to reload the auction tables from the DB so a
    just-inserted listing becomes visible in-game without a restart."""
    return command("reload auctions")
