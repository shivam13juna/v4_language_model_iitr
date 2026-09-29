"""The tests you can run on a plane.

Not one of these calls a model: the model is `scripted:`. A suite that needs an API key
and forty seconds is a suite nobody runs before pushing. What is left still covers what
actually breaks — the contract, the scoping, the read-only promise, and what the lookup is
and is not allowed to claim.

    pytest porter/tests -q
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from porter import agent as porter_agent
from porter.schemas import Answer, Usage
from porter.settings import get_settings
from porter.tests.conftest import CUSTOMER, bearer
from porter.tools import NO_TRACKING, build_tools, read_only

THEIR_ORDER = "580638"      # 7 lines, 147.01
SOMEONE_ELSES = "536365"    # a real order, on a different account


@pytest.fixture
def tools(tmp_path):
    return {t.name: t for t in build_tools(CUSTOMER, get_settings().db_path,
                                           tmp_path / "ledger.sqlite", "test-key")}


def ask(client, question="hello", **fields):
    return client.post("/v1/chat", json={"question": question, **fields}, headers=bearer(CUSTOMER))


# --- the contract ----------------------------------------------------------------------


def test_empty_question_is_rejected_before_the_model(make_client):
    with make_client() as c:
        r = ask(c, "")
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"


def test_oversized_question_is_rejected(make_client):
    with make_client() as c:
        assert ask(c, "x" * 2001).status_code == 422


def test_every_error_carries_a_request_id(make_client):
    with make_client() as c:
        r = c.get("/v1/threads/does-not-exist", headers=bearer(CUSTOMER))
    assert r.status_code == 404
    assert r.json()["request_id"].startswith("req-")
    assert r.headers["x-request-id"] == r.json()["request_id"]


def test_every_response_says_which_version_answered(make_client):
    with make_client(PORTER_VERSION="2.0.0") as c:
        assert c.get("/healthz").headers["x-porter-version"] == "2.0.0"
        assert c.get("/version").json()["version"] == "2.0.0"


def test_an_internal_failure_leaks_nothing(make_client, monkeypatch):
    async def explode(*a, **k):
        raise RuntimeError(f"connection to {get_settings().db_path} failed: SELECT * FROM invoices")

    monkeypatch.setattr(porter_agent, "aanswer", explode)
    make_client()                           # configures the environment
    from fastapi.testclient import TestClient
    from porter.api import app

    # TestClient re-raises server errors by default; here the response is what is under test.
    with TestClient(app, raise_server_exceptions=False) as c:
        r = ask(c)
    assert r.status_code == 500 and r.json()["error"] == "internal"
    assert "invoices" not in r.text and "online_retail" not in r.text


def test_a_model_outage_is_a_503_not_a_500(make_client, monkeypatch):
    async def refuse(*a, **k):
        raise httpx.ConnectError("all connection attempts failed")

    monkeypatch.setattr(porter_agent, "aanswer", refuse)
    with make_client() as c:
        r = ask(c)
    assert r.status_code == 503 and r.json()["error"] == "model_unavailable"


def test_chat_returns_the_promised_shape(make_client):
    with make_client() as c:
        r = ask(c)
    assert r.status_code == 200
    body = Answer(**r.json())
    assert body.thread_id.startswith("t-") and body.usage.model_calls == 1


def test_health_checks_each_dependency(make_client, tmp_path):
    with make_client(PORTER_DB_PATH=str(tmp_path / "missing.db")) as c:
        health = c.get("/healthz").json()
    assert health["status"] == "degraded"
    assert health["checks"]["database"].startswith("failed")


# --- the tools -------------------------------------------------------------------------


def test_a_customer_cannot_reach_someone_elses_order(tools):
    assert THEIR_ORDER in tools["look_up_order"].invoke({"invoice_no": THEIR_ORDER})
    assert "No order" in tools["look_up_order"].invoke({"invoice_no": SOMEONE_ELSES})


def test_the_lookup_does_not_claim_what_the_data_does_not_hold(tools):
    """`country` is the country on the account. There is no shipping field at all."""
    out = tools["look_up_order"].invoke({"invoice_no": THEIR_ORDER})
    assert "shipped" not in out.lower() and "norway" not in out.lower()
    assert NO_TRACKING in out


def test_price_comes_from_the_database(tools):
    assert "147.01" in tools["price_order"].invoke({"invoice_no": THEIR_ORDER})


def test_the_reading_connection_cannot_write():
    con = read_only(get_settings().db_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DROP TABLE products")
    con.close()


def test_a_cancelled_order_cannot_be_returned(tools):
    assert "cancelled" in tools["start_return"].invoke({"invoice_no": "C565050", "reason": "meh"})


def test_answer_works_outside_any_web_process(tmp_path):
    from porter.settings import Settings

    s = Settings(model="scripted:lookup", checkpoint_path=tmp_path / "t.sqlite",
                 ledger_path=tmp_path / "l.sqlite")
    out = porter_agent.answer("Where is 580638?", CUSTOMER, settings=s)
    assert out.tools_used == ["look_up_order"] and THEIR_ORDER in out.reply
    assert isinstance(out.usage, Usage) and out.usage.input_tokens == 240


def test_a_run_that_was_never_answered_is_never_rescored():
    """A 504 has an empty reply, and an empty reply passes every "must not say X" check."""
    from porter.evals import Report, Result, rescore

    case = {"id": "b-004", "kind": "injection", "expect": {"no_tools": ["start_return"]}}
    timed_out = Result("b-004", "injection", False, "http 504", status=504)
    assert rescore(Report([timed_out]), [case]).results[0].passed is False


def test_a_correct_date_written_out_is_not_a_wrong_answer():
    """The cases expect 2010-12-09; gpt-oss writes "9 December 2010" with U+202F between the parts."""
    from porter.evals import score

    case = {"expect": {"contains_all": ["537967", "2010-12-09"]}}
    for reply in ("Order 537967 was placed on 9 December 2010.",
                  "Order 537967 was placed on December 9th, 2010.",
                  "Order 537967 was placed on 2010-12-09 10:12:00."):
        assert score(case, reply, []) == (True, "")
    assert score(case, "Order 537967 was placed on 19 December 2010.", [])[0] is False


def test_a_limit_that_ends_the_run_says_so_in_words_a_customer_can_read(tmp_path):
    from porter.agent import LIMIT_REPLY
    from porter.settings import Settings

    s = Settings(model="scripted:lookup", max_model_calls=1, checkpoint_path=tmp_path / "t.sqlite",
                 ledger_path=tmp_path / "l.sqlite")
    out = porter_agent.answer("Where is 580638?", CUSTOMER, settings=s)
    assert out.stopped_at_limit is True
    assert out.usage.model_calls == 1                      # the limit's own message is not a model call
    assert out.reply == LIMIT_REPLY and "exceeded" not in out.reply
