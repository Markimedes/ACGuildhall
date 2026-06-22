"""Gemini-backed generation for the Guildhall news desk.

Turns a prompt built by ``news_prompts`` into a finished article dict. This module
owns the model call and the parsing; the prompt text and reporter personas live in
``news_prompts``.

The news desk is a ``NewsDesk`` instance configured through its constructor::

    desk = NewsDesk(api_key=cfg["key"], model="gemini-3-flash-preview")
    article = desk.generate_market_article("professional_digest", events)

Like the other service clients here (see ``ahservice.py``), every failure path is
graceful: if the key is unset, the SDK is missing, the model errors or returns
unparseable output, generation returns ``None`` and the caller simply shows no
fresh article -- the page still renders.

A generated article is::

    {
      "category": "professional_digest",   # the section it belongs to
      "headline": str,
      "dek": str,                            # subheading/standfirst
      "content": str,                        # body, blank-line-separated paragraphs
      "author": str,                         # reporter byline (authoritative)
      "author_title": str,                   # reporter beat/role
      "dateline": str,                       # town it's filed from (may be "")
    }
"""

from __future__ import annotations

import json
import logging
import os
import re

import news_prompts
from news_prompts import Reporter

log = logging.getLogger("guildhall.news")

DEFAULT_MODEL = "gemini-3-flash-preview"

# The required keys a parsed article must carry before we'll trust it.
_REQUIRED = ("headline", "content")

# Match a JSON object even when the model wraps it in ```json fences or chatter.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class NewsDesk:
    """Generates in-universe news articles via Gemini.

    All configuration is passed to the constructor:

      api_key   the Google AI Studio key. Falsy -> the desk is disabled (every
                generate call returns None); construction never raises.
      model     model id (default "gemini-3-flash-preview").
      thinking  thinking level: HIGH / MEDIUM / LOW (default HIGH).
      timeout   per-call timeout in seconds (default 30).

    The SDK is imported and the client built lazily on first use, so constructing
    a desk is cheap and works even if google-genai isn't installed (the desk just
    stays dark).
    """

    def __init__(self, api_key: str | None, *, model: str = DEFAULT_MODEL,
                 thinking: str = "HIGH", timeout: float = 30.0):
        self.api_key = (api_key or "").strip()
        self.model = model
        self.thinking = (thinking or "").upper()
        self.timeout = float(timeout)
        self._client = None
        self._types = None
        self._loaded = False  # guards against retrying a failed import/build

    @classmethod
    def from_env(cls, env=os.environ) -> "NewsDesk":
        """Build a desk from GUILDHALL_GEMINI_* env vars -- a convenience for the
        app's normal startup. The constructor remains the canonical entry point."""
        return cls(
            api_key=env.get("GUILDHALL_GEMINI_API_KEY"),
            model=env.get("GUILDHALL_GEMINI_MODEL", DEFAULT_MODEL),
            thinking=env.get("GUILDHALL_GEMINI_THINKING", "HIGH"),
            timeout=float(env.get("GUILDHALL_GEMINI_TIMEOUT", "30")),
        )

    # --- model plumbing ------------------------------------------------------
    def _load(self):
        """Import the SDK and build the client once. Returns the client or None if
        the key is unset or the SDK isn't installed/usable."""
        if self._loaded:
            return self._client
        self._loaded = True
        if not self.api_key:
            return None
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            log.warning("google-genai not installed; news desk disabled")
            return None
        self._types = types
        try:
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
            )
        except Exception:  # noqa: BLE001 -- bad key/config -> desk stays dark
            log.exception("failed to construct Gemini client")
            self._client = None
        return self._client

    def available(self) -> bool:
        """Whether the desk can generate (key present and SDK importable)."""
        return self._load() is not None

    def _config(self):
        """The GenerateContentConfig: force JSON out, set the thinking level."""
        types = self._types
        kwargs = {"response_mime_type": "application/json"}
        if self.thinking in ("HIGH", "MEDIUM", "LOW"):
            # Guard: older SDKs may not expose ThinkingConfig/thinking_level.
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level=self.thinking)
            except Exception:  # noqa: BLE001
                pass
        return types.GenerateContentConfig(**kwargs)

    def _generate_text(self, prompt: str) -> str | None:
        """One model round-trip. Returns the raw response text, or None on error."""
        client = self._load()
        if client is None:
            return None
        try:
            resp = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self._config(),
            )
        except Exception:  # noqa: BLE001 -- network/model error -> no article
            log.exception("Gemini generate_content failed")
            return None
        return getattr(resp, "text", None)

    # --- generation ----------------------------------------------------------
    def generate_article(self, category: str, reporter: Reporter,
                         prompt: str) -> dict | None:
        """Run ``prompt`` and assemble the finished article dict, or None.

        The byline is taken from ``reporter`` (we know who we asked), not from the
        model output -- the model only supplies headline/dek/content.
        """
        obj = _parse_article(self._generate_text(prompt) or "")
        if obj is None:
            return None
        return {
            "category": category,
            "headline": str(obj["headline"]).strip(),
            "dek": str(obj.get("dek", "")).strip(),
            "content": str(obj["content"]).strip(),
            "author": reporter.byline,
            "author_title": reporter.title,
            "dateline": reporter.dateline,
        }

    def generate_market_article(self, category: str, events: dict,
                                seed=None) -> dict | None:
        """Generate a market story (Professional Digest / Gear For You / Primary
        Stats) from today's ``ahservice.events()`` dict. Pass a ``seed`` (e.g. the
        date) to make the reporter pick stable."""
        reporter = news_prompts.pick_reporter(category, seed=seed)
        prompt = news_prompts.market_prompt(category, reporter, events)
        return self.generate_article(category, reporter, prompt)

    def generate_exploits_article(self, exploits: dict,
                                  seed=None) -> dict | None:
        """Generate a Heroic Exploits story about ONE character (see
        ``news_prompts.exploits_prompt`` for the expected shape)."""
        cat = news_prompts.HEROIC_EXPLOITS
        reporter = news_prompts.pick_reporter(cat, seed=seed)
        prompt = news_prompts.exploits_prompt(reporter, exploits)
        return self.generate_article(cat, reporter, prompt)

    def generate_group_exploits_article(self, group: dict,
                                        seed=None) -> dict | None:
        """Generate a Heroic Exploits story about SEVERAL characters who shared the
        same feat the same day (see ``news_prompts.group_exploits_prompt``)."""
        cat = news_prompts.HEROIC_EXPLOITS
        reporter = news_prompts.pick_reporter(cat, seed=seed)
        prompt = news_prompts.group_exploits_prompt(reporter, group)
        return self.generate_article(cat, reporter, prompt)

    def generate_obituary_article(self, subject: dict,
                                  seed=None) -> dict | None:
        """Generate an Obituary commemorating ONE fallen character (see
        ``news_prompts.obituary_prompt`` for the expected shape)."""
        cat = news_prompts.OBITUARIES
        reporter = news_prompts.pick_reporter(cat, seed=seed)
        prompt = news_prompts.obituary_prompt(reporter, subject)
        return self.generate_article(cat, reporter, prompt)


# --- parsing (stateless; shared) ---------------------------------------------
def _parse_article(text: str) -> dict | None:
    """Pull the article object out of the model's response. Tolerates code fences
    and stray prose around the JSON. Returns None if no valid object is found."""
    if not text:
        return None
    candidate = text.strip()
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        m = _JSON_RE.search(candidate)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    if any(not str(obj.get(k, "")).strip() for k in _REQUIRED):
        log.warning("model article missing required keys: %s", list(obj))
        return None
    return obj
