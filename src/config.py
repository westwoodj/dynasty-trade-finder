"""Configuration helpers for the Dynasty Trade Finder.

Two API keys are resolved here:

* **Parse** (``get_parse_api_key``) powers the typed ``parse_apis`` clients
  (FantasyCalc, DraftSharks, KeepTradeCut, …).
* **SportsDataIO** (``get_sportsdata_api_key``) powers the NFL performance /
  projection endpoints.

Both follow the same precedence: ``.streamlit/secrets.toml`` → environment
variable.  A ``.env`` file at the repo root is loaded on import (via
``python-dotenv``) so secrets can live there instead of the shell; see
``.env.example``.  ``.env`` is gitignored — never commit real keys.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

# Populate os.environ from a repo-root .env once, at import time. Existing
# environment variables win (override=False), and a missing file is a no-op.
load_dotenv(override=False)


def _secret(section: str, key: str) -> Optional[str]:
    """Read a Streamlit secret, returning ``None`` when running outside
    Streamlit or when the secrets file / section / key is absent."""
    try:
        import streamlit as st

        value = st.secrets[section][key]
    except Exception:
        return None
    return str(value) if value else None


def get_parse_api_key() -> Optional[str]:
    """Resolve the Parse API key from secrets or the environment."""
    for candidate in (
        _secret("parse", "api_key"),
        os.environ.get("PARSE_API_KEY"),
        _secret("parse_bot", "api_key"),
        os.environ.get("PARSE_BOT_API_KEY"),
    ):
        if candidate:
            return candidate
    return None


def get_sportsdata_api_key() -> Optional[str]:
    """Resolve the SportsDataIO API key from secrets or the environment."""
    for candidate in (
        _secret("sportsdata", "api_key"),
        os.environ.get("SPORTSDATA_API_KEY"),
    ):
        if candidate:
            return candidate
    return None
