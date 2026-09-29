"""The two pages and what they call beyond the chat: the customer's orders, who a token belongs
to, and the demo sign-in that stands in for a login page on a laptop."""

from __future__ import annotations

from porter import ledger
from porter.tests.conftest import CUSTOMER, OTHER, bearer


def test_the_order_list_is_the_signed_in_customers_own(make_client):
    with make_client() as c:
        mine = c.get("/v1/orders", headers=bearer(CUSTOMER)).json()
        theirs = c.get("/v1/orders", headers=bearer(OTHER)).json()
    assert [o["invoice_no"] for o in mine] == ["580638", "574694", "570725", "570681", "C565050", "563100"]
    assert {o["invoice_no"] for o in mine}.isdisjoint(o["invoice_no"] for o in theirs)
    latest = mine[0]
    assert (latest["total"], latest["lines"], latest["cancelled"]) == (147.01, 7, False)
    assert next(o for o in mine if o["invoice_no"] == "C565050")["cancelled"] is True


def test_the_order_list_needs_a_customer_token(make_client):
    with make_client() as c:
        assert c.get("/v1/orders").status_code == 401
        assert c.get("/v1/orders", headers=bearer(staff="alice")).status_code == 403


def test_an_order_shows_its_return_and_then_its_refund(make_client, ledger_path):
    with make_client() as c:
        opened, _ = ledger.request_return(ledger_path, key="k-1", invoice_no="574694",
                                          customer_id=CUSTOMER, amount=419.06, reason="arrived broken")

        def order():
            return next(o for o in c.get("/v1/orders", headers=bearer(CUSTOMER)).json()
                        if o["invoice_no"] == "574694")

        assert order()["return_request"]["status"] == "pending"
        c.post(f"/v1/returns/{opened['return_id']}/approve", headers=bearer(staff="alice"))
        paid = order()["return_request"]
        listed = c.get("/v1/returns", headers=bearer(staff="alice")).json()
    assert paid["status"] == "approved" and paid["refund_id"].startswith("F-")
    assert listed[0]["refund_id"] == paid["refund_id"]          # the staff list carries the refund too


def test_me_says_who_a_token_belongs_to(make_client):
    with make_client() as c:
        assert c.get("/v1/me", headers=bearer(CUSTOMER)).json() == {
            "subject": "customer:12381", "role": "customer", "customer_id": 12381.0}
        assert c.get("/v1/me", headers=bearer(staff="alice")).json()["role"] == "staff"
        assert c.get("/v1/me").status_code == 401
        assert c.get("/v1/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_demo_sign_in_hands_out_both_kinds_of_token_only_when_switched_on(make_client):
    with make_client(PORTER_DEV_LOGIN="true") as c:
        staff = c.post("/dev/token", json={"staff": "alice"}).json()["token"]
        assert c.get("/v1/me", headers={"Authorization": f"Bearer {staff}"}).json()["role"] == "staff"
        assert c.post("/dev/token", json={"customer_id": 12381}).status_code == 200
        assert c.post("/dev/token", json={}).status_code == 422
        assert c.post("/dev/token", json={"customer_id": 12381, "staff": "alice"}).status_code == 422
        assert c.get("/version").json()["demo_sign_in"] is True
    with make_client(PORTER_DEV_LOGIN="false") as c:
        assert c.post("/dev/token", json={"staff": "alice"}).status_code == 404
        assert c.get("/version").json()["demo_sign_in"] is False


def test_both_pages_and_what_they_load_are_served(make_client):
    with make_client() as c:
        for path in ("/", "/staff", "/web/desk.css", "/web/desk.js"):
            r = c.get(path)
            assert r.status_code == 200, path
        assert "Returns desk" in c.get("/staff").text
