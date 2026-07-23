"""Tests for src.config — Parse + SportsDataIO API key resolution."""

import pytest
import streamlit

from src.config import (
    get_parse_api_key,
    get_sportsdata_api_key,
    set_parse_api_key_env,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Isolate each test from real env vars and secrets."""
    monkeypatch.delenv("PARSE_API_KEY", raising=False)
    monkeypatch.delenv("PARSE_BOT_API_KEY", raising=False)
    monkeypatch.delenv("SPORTSDATA_API_KEY", raising=False)
    monkeypatch.setattr(streamlit, "secrets", {}, raising=False)


def test_returns_none_when_nothing_configured():
    assert get_parse_api_key() is None


def test_env_var_is_used(monkeypatch):
    monkeypatch.setenv("PARSE_API_KEY", "env-key")
    assert get_parse_api_key() == "env-key"


def test_secrets_win_over_env(monkeypatch):
    monkeypatch.setenv("PARSE_API_KEY", "env-key")
    monkeypatch.setattr(
        streamlit, "secrets", {"parse": {"api_key": "secrets-key"}}, raising=False
    )
    assert get_parse_api_key() == "secrets-key"


def test_legacy_secrets_section(monkeypatch):
    monkeypatch.setattr(
        streamlit, "secrets", {"parse_bot": {"api_key": "legacy-key"}}, raising=False
    )
    assert get_parse_api_key() == "legacy-key"


def test_legacy_env_var(monkeypatch):
    monkeypatch.setenv("PARSE_BOT_API_KEY", "legacy-env-key")
    assert get_parse_api_key() == "legacy-env-key"


def test_new_env_wins_over_legacy_secret(monkeypatch):
    monkeypatch.setenv("PARSE_API_KEY", "env-key")
    monkeypatch.setattr(
        streamlit, "secrets", {"parse_bot": {"api_key": "legacy-key"}}, raising=False
    )
    assert get_parse_api_key() == "env-key"


def test_empty_values_are_skipped(monkeypatch):
    monkeypatch.setattr(
        streamlit, "secrets", {"parse": {"api_key": ""}}, raising=False
    )
    monkeypatch.setenv("PARSE_API_KEY", "")
    monkeypatch.setenv("PARSE_BOT_API_KEY", "fallback-key")
    assert get_parse_api_key() == "fallback-key"


def test_set_parse_api_key_env(monkeypatch):
    set_parse_api_key_env("my-key")
    import os

    assert os.environ["PARSE_API_KEY"] == "my-key"


def test_set_parse_api_key_env_ignores_empty(monkeypatch):
    set_parse_api_key_env("")
    import os

    assert "PARSE_API_KEY" not in os.environ


# ---------------------------------------------------------------------------
# SportsDataIO key resolution
# ---------------------------------------------------------------------------


def test_sportsdata_none_when_unconfigured():
    assert get_sportsdata_api_key() is None


def test_sportsdata_env_var(monkeypatch):
    monkeypatch.setenv("SPORTSDATA_API_KEY", "sd-env")
    assert get_sportsdata_api_key() == "sd-env"


def test_sportsdata_secrets_win_over_env(monkeypatch):
    monkeypatch.setenv("SPORTSDATA_API_KEY", "sd-env")
    monkeypatch.setattr(
        streamlit, "secrets", {"sportsdata": {"api_key": "sd-secret"}}, raising=False
    )
    assert get_sportsdata_api_key() == "sd-secret"
