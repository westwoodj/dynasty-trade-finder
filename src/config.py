"""Configuration helpers for the Dynasty Trade Finder.

The Parse API key powers the typed ``parse_apis`` clients (FantasyCalc,
DraftSharks, KeepTradeCut, …).  It can be supplied via, in order of
precedence:

1. ``.streamlit/secrets.toml``::

       [parse]
       api_key = "your-key-here"

2. The ``PARSE_API_KEY`` environment variable (also what ``parse_sdk``
   reads natively).
3. Legacy locations kept for backwards compatibility: the
   ``[parse_bot]`` secrets section and the ``PARSE_BOT_API_KEY``
   environment variable.
"""

from __future__ import annotations

import os
from typing import Optional


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


def set_parse_api_key_env(api_key: str) -> None:
    """Expose *api_key* as ``PARSE_API_KEY`` so parse_sdk clients constructed
    without an explicit key still authenticate."""
    if api_key:
        os.environ["PARSE_API_KEY"] = api_key
