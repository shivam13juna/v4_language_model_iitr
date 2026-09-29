"""The provider switch, tested without a model or a network.

PORTER_PROVIDER decides the model, the address and the key, unless the environment sets them
itself. These pin down the two promises that matter: one setting swaps OpenAI for Ollama, and
the OpenAI key is never sent anywhere but OpenAI.

    pytest porter/tests/test_provider.py -q
"""

from __future__ import annotations

import asyncio
import os

import pytest

from porter.health import check
from porter.settings import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """Nothing from this machine's environment or .env: only what each test sets."""
    for name in list(os.environ):
        if name.startswith("PORTER_") or name == "OPENAI_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.setenv("PORTER_LEDGER_PATH", str(tmp_path / "ledger.sqlite"))
    monkeypatch.setenv("PORTER_CHECKPOINT_PATH", str(tmp_path / "threads.sqlite"))


def test_openai_is_the_default_and_reads_the_standard_key_name(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = Settings(_env_file=None)
    assert (s.provider, s.model, s.base_url, s.api_key) == (
        "openai", "gpt-4.1-mini", "https://api.openai.com/v1", "sk-test")


def test_one_setting_swaps_to_ollama_and_the_openai_key_stays_behind(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PORTER_PROVIDER", "ollama")
    s = Settings(_env_file=None)
    assert (s.model, s.base_url, s.api_key) == ("gpt-oss:20b", "http://127.0.0.1:11434/v1", "ollama")


def test_explicit_values_win_so_the_tunnel_still_works(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PORTER_PROVIDER", "ollama")
    monkeypatch.setenv("PORTER_BASE_URL", "https://example.ngrok-free.app/v1")
    monkeypatch.setenv("PORTER_API_KEY", "tunnel-token")
    s = Settings(_env_file=None)
    assert (s.model, s.base_url, s.api_key) == ("gpt-oss:20b", "https://example.ngrok-free.app/v1", "tunnel-token")


def test_a_provider_that_does_not_exist_stops_the_start(monkeypatch):
    monkeypatch.setenv("PORTER_PROVIDER", "Ollama")
    with pytest.raises(ValueError, match="provider"):
        Settings(_env_file=None)


def test_the_health_check_names_a_missing_key():
    """Without this, the empty key is sent as `Bearer ` and the check reads 'unreachable'."""
    checks = asyncio.run(check(Settings(_env_file=None)))
    assert checks["model"] == "no API key: set OPENAI_API_KEY"
