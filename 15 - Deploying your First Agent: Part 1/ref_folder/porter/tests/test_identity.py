"""Who is calling, and whose conversation is it.

The contract used to take `customer_id` in the body and resolve any `thread_id` it was
given. These tests pin the replacement: a signed token decides the customer, and a
conversation id only ever resolves inside that customer's own namespace.
"""

from __future__ import annotations

import time

import jwt

from porter.auth import ALGORITHM
from porter.tests.conftest import CUSTOMER, OTHER, bearer


def ask(client, question="Where is my order 580638?", headers=None, **fields):
    return client.post("/v1/chat", json={"question": question, **fields}, headers=headers or {})


def test_no_token_is_a_401(make_client):
    with make_client() as c:
        r = ask(c)
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_a_token_this_desk_did_not_sign_is_a_401(make_client):
    with make_client() as c:
        r = ask(c, headers=bearer(CUSTOMER, secret="somebody-elses-secret-of-decent-length!!"))
    assert r.status_code == 401


def test_an_expired_token_is_a_401(make_client):
    from porter.settings import get_settings

    with make_client() as c:
        stale = jwt.encode({"sub": "customer:12381", "role": "customer", "exp": int(time.time()) - 5},
                           get_settings().auth_secret, algorithm=ALGORITHM)
        r = ask(c, headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]


def test_a_staff_token_does_not_chat_as_a_customer(make_client):
    with make_client() as c:
        assert ask(c, headers=bearer(staff="alice")).status_code == 403


def test_naming_someone_else_in_the_body_is_refused(make_client):
    with make_client() as c:
        r = ask(c, headers=bearer(OTHER), customer_id=CUSTOMER)
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"


def test_another_customers_conversation_cannot_be_read_or_continued(make_client):
    with make_client(PORTER_MODEL="scripted:lookup") as c:
        mine = ask(c, headers=bearer(CUSTOMER)).json()["thread_id"]

        assert c.get(f"/v1/threads/{mine}", headers=bearer(CUSTOMER)).status_code == 200
        read = c.get(f"/v1/threads/{mine}", headers=bearer(OTHER))
        cont = ask(c, "and what did that cost?", headers=bearer(OTHER), thread_id=mine)

    # 404, not 403: "that is not yours" would confirm that it exists.
    assert read.status_code == 404 and read.json()["error"] == "thread_not_found"
    assert cont.status_code == 404


def test_an_unknown_thread_is_a_404_not_a_new_conversation(make_client):
    with make_client() as c:
        r = ask(c, headers=bearer(CUSTOMER), thread_id="t-made-up")
    assert r.status_code == 404


def test_the_trusted_mode_needs_a_customer_id(make_client):
    """With tokens off, the body is believed — which is only acceptable behind another
    service that has already signed the customer in."""
    with make_client(PORTER_AUTH_REQUIRED="false") as c:
        assert ask(c).status_code == 422
        assert ask(c, customer_id=CUSTOMER).status_code == 200


def test_staff_routes_need_staff_even_in_trusted_mode(make_client):
    with make_client(PORTER_AUTH_REQUIRED="false") as c:
        assert c.get("/v1/returns").status_code == 401
        assert c.get("/v1/returns", headers=bearer(CUSTOMER)).status_code == 403
        assert c.get("/v1/returns", headers=bearer(staff="alice")).status_code == 200


def test_a_conversation_survives_a_restart(make_client):
    """Two app lifetimes, one disk: the second process has never seen the conversation."""
    with make_client(PORTER_MODEL="scripted:lookup") as first:
        thread = ask(first, headers=bearer(CUSTOMER)).json()["thread_id"]

    with make_client(PORTER_MODEL="scripted:lookup") as second:
        remembered = second.get(f"/v1/threads/{thread}", headers=bearer(CUSTOMER))
        follow = ask(second, "and when was that?", headers=bearer(CUSTOMER), thread_id=thread)

    assert remembered.status_code == 200 and remembered.json()["turns"] >= 4
    assert follow.status_code == 200
