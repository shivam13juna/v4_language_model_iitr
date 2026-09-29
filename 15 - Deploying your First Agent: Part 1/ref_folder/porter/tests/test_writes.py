"""Writes: who may pay a refund, what a retry does, and what a timeout leaves behind.

Every assertion here reads the ledger, not the agent's reply. A reply can say anything;
the ledger is what happened.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from porter import ledger
from porter.agent import answer
from porter.settings import Settings, get_settings
from porter.tests.conftest import CUSTOMER, bearer
from porter.tools import build_tools


def chat(client, question="hello", headers=None):
    return client.post("/v1/chat", json={"question": question}, headers=headers or bearer(CUSTOMER))


# --- the approval policy -----------------------------------------------------------------


def test_the_agent_can_only_ask_for_a_refund(make_client, ledger_path):
    """A model that opens a return unprompted — scripted to, here — produces a pending
    request and no money."""
    with make_client(PORTER_MODEL="scripted:refund") as c:
        r = chat(c)
    assert r.status_code == 200 and "start_return" in r.json()["tools_used"]
    assert ledger.counts(ledger_path) == {"pending": 1, "approved": 0, "rejected": 0,
                                          "refunds": 0, "refunded": 0.0}


def test_a_customer_cannot_approve_their_own_refund(make_client, ledger_path):
    with make_client(PORTER_MODEL="scripted:refund") as c:
        chat(c)
        (pending,) = ledger.list_returns(ledger_path, "pending")
        r = c.post(f"/v1/returns/{pending['return_id']}/approve", headers=bearer(CUSTOMER))
    assert r.status_code == 403
    assert ledger.counts(ledger_path)["refunds"] == 0


def test_approving_twice_pays_once(make_client, ledger_path):
    with make_client(PORTER_MODEL="scripted:refund") as c:
        chat(c)
        (pending,) = ledger.list_returns(ledger_path, "pending")
        url = f"/v1/returns/{pending['return_id']}/approve"
        first = c.post(url, headers=bearer(staff="alice"))
        again = c.post(url, headers=bearer(staff="alice"))

    assert first.status_code == 200 and first.json()["replayed"] is False
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["refund_id"] == first.json()["refund_id"]
    assert ledger.counts(ledger_path)["refunds"] == 1
    assert ledger.counts(ledger_path)["refunded"] == 147.01


def test_an_order_is_refunded_at_most_once_whoever_asks(ledger_path):
    """Two different requests for the same order — two sessions, two keys. Both can be
    pending; only one can be paid."""
    for key in ("session-a", "session-b"):
        ledger.request_return(ledger_path, key=key, invoice_no="580638", customer_id=CUSTOMER,
                              amount=147.01, reason="broken")
    a, b = ledger.list_returns(ledger_path, "pending")
    ledger.approve(ledger_path, a["return_id"], "staff:alice")
    with pytest.raises(ledger.LedgerError) as refused:
        ledger.approve(ledger_path, b["return_id"], "staff:bob")
    assert refused.value.code == "conflict"
    assert ledger.counts(ledger_path)["refunds"] == 1


def test_a_rejected_return_cannot_be_paid(ledger_path):
    row, _ = ledger.request_return(ledger_path, key="k", invoice_no="574694", customer_id=CUSTOMER,
                                   amount=419.06, reason="changed my mind")
    ledger.reject(ledger_path, row["return_id"], "staff:alice")
    with pytest.raises(ledger.LedgerError):
        ledger.approve(ledger_path, row["return_id"], "staff:alice")


# --- the same request, twice -------------------------------------------------------------


def test_the_same_request_sent_twice_opens_one_return(tmp_path):
    tools = {t.name: t for t in build_tools(CUSTOMER, get_settings().db_path, tmp_path / "l.sqlite",
                                            idempotency_key="order-574694-return-a41f")}
    first = tools["start_return"].invoke({"invoice_no": "574694", "reason": "broken"})
    second = tools["start_return"].invoke({"invoice_no": "574694", "reason": "broken"})
    assert "opened" in first and "repeat" in second
    assert ledger.counts(tmp_path / "l.sqlite")["pending"] == 1


def test_without_a_key_a_retry_is_a_second_request(tmp_path):
    """The reason clients send an Idempotency-Key: without one, the service cannot tell a
    retry from a second request."""
    tools = {t.name: t for t in build_tools(CUSTOMER, get_settings().db_path, tmp_path / "l.sqlite")}
    tools["start_return"].invoke({"invoice_no": "574694", "reason": "broken"})
    tools["start_return"].invoke({"invoice_no": "574694", "reason": "broken"})
    assert ledger.counts(tmp_path / "l.sqlite")["pending"] == 2


# --- time -------------------------------------------------------------------------------


def test_a_thread_keeps_going_after_its_caller_gives_up(tmp_path):
    """The hazard: `wait_for` around a blocking call in a thread. The caller gets its
    timeout at 0.3s; the thread does not know, and opens the return anyway."""
    settings = Settings(model="scripted:refund:0.6", request_timeout_s=5, model_timeout_s=0.1,
                        checkpoint_path=tmp_path / "t.sqlite",
                        ledger_path=tmp_path / "l.sqlite")

    async def impatient_caller():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.to_thread(answer, "hello", CUSTOMER, settings=settings), 0.3)
        return ledger.counts(settings.ledger_path)["pending"]

    at_timeout = asyncio.run(impatient_caller())        # asyncio.run waits for the thread to end
    assert at_timeout == 0
    assert ledger.counts(settings.ledger_path)["pending"] == 1


def test_after_a_504_nothing_is_left_running(make_client, ledger_path):
    """The fix: the run is awaited, so the timeout cancels it. Same script, same timing."""
    with make_client(PORTER_MODEL="scripted:refund:0.6", PORTER_REQUEST_TIMEOUT_S="0.3",
                     PORTER_MODEL_TIMEOUT_S="0.1") as c:
        r = chat(c)
        time.sleep(1.5)                     # longer than the script needed to open the return
    assert r.status_code == 504 and r.json()["error"] == "timeout"
    assert ledger.counts(ledger_path)["pending"] == 0


def test_the_write_refuses_to_start_after_the_deadline(tmp_path):
    tools = {t.name: t for t in build_tools(CUSTOMER, get_settings().db_path, tmp_path / "l.sqlite",
                                            idempotency_key="k", deadline=time.monotonic() - 1)}
    out = tools["start_return"].invoke({"invoice_no": "574694", "reason": "broken"})
    assert "no return was opened" in out
    assert ledger.counts(tmp_path / "l.sqlite")["pending"] == 0


def test_a_downstream_timeout_that_outlives_the_request_will_not_start():
    with pytest.raises(ValueError, match="does not fit"):
        Settings(request_timeout_s=60, model_timeout_s=90)


def test_a_timeout_is_not_retried_and_a_dropped_connection_is():
    """Retrying a call that timed out on a busy model re-queues the same work. Retrying one
    whose connection dropped is exactly what retries are for."""
    import httpx
    import openai

    from porter.agent import _transient

    request = httpx.Request("POST", "http://model/v1/chat/completions")
    assert _transient(openai.APIConnectionError(request=request))
    assert not _transient(openai.APITimeoutError(request=request))
    assert not _transient(ValueError("a bug is not transient"))
