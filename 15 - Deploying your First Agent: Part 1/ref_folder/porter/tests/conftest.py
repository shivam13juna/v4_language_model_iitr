"""Shared fixtures. Every test here runs without a model: the service is real, the model is
`scripted:` — see porter/scripted.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from porter import security
from porter.api import app
from porter.auth import issue_token
from porter.settings import get_settings

CUSTOMER = 12381.0          # Norway, six orders
OTHER = 12490.0             # a different customer, with orders of their own


def bearer(customer_id: float | None = None, staff: str | None = None, secret: str | None = None) -> dict:
    return {"Authorization": f"Bearer {issue_token(customer_id=customer_id, staff=staff, secret=secret)}"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """A TestClient over the real app, configured through the environment the way a
    container would be. Called twice in one test, it is two processes sharing one disk —
    which is how a restart is tested."""

    def make(**env) -> TestClient:
        settings = {
            "PORTER_MODEL": "scripted:answer",
            "PORTER_CHECKPOINT_PATH": str(tmp_path / "threads.sqlite"),
            "PORTER_LEDGER_PATH": str(tmp_path / "ledger.sqlite"),
        }
        settings.update(env)
        for key, value in settings.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        security.QUOTA.reset()
        return TestClient(app)

    yield make
    get_settings.cache_clear()


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "ledger.sqlite"
