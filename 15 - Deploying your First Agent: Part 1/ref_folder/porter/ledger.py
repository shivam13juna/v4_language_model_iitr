"""Return requests and refunds — the only place in the service where money moves.

Two tables, because asking for a refund and paying one are two different events with two
different people behind them:

    returns    what the desk opened on a customer's behalf. Always `pending` first.
    refunds    what a member of staff approved. Written only by `approve`, never by a tool.

The agent can reach the first table and cannot reach the second. That is the approval
policy, and it lives here, in the schema and in which function writes where — not in the
prompt, which is a request to the model rather than a rule the service enforces.

Three constraints do the work that an `if` would get wrong under a retry:

    returns.idempotency_key UNIQUE    the same request sent twice opens one return
    refunds.return_id       UNIQUE    the same approval sent twice pays once
    refunds.invoice_no      UNIQUE    an order is refunded at most once, whoever asks

An `if` checks and then writes; two copies of the same request both pass the check before
either writes. A constraint is checked by the database at the moment of the write, which is
the only moment that matters.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_ledger(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS returns (
            return_id        TEXT PRIMARY KEY,
            idempotency_key  TEXT UNIQUE NOT NULL,
            invoice_no       TEXT NOT NULL,
            customer_id      REAL NOT NULL,
            amount           REAL NOT NULL,
            reason           TEXT,
            status           TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected')),
            requested_at     TEXT NOT NULL,
            decided_by       TEXT,
            decided_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS refunds (
            refund_id    TEXT PRIMARY KEY,
            return_id    TEXT UNIQUE NOT NULL REFERENCES returns(return_id),
            invoice_no   TEXT UNIQUE NOT NULL,
            customer_id  REAL NOT NULL,
            amount       REAL NOT NULL,
            approved_by  TEXT NOT NULL,
            paid_at      TEXT NOT NULL
        );
        """
    )
    con.commit()
    return con


def request_return(path: Path, *, key: str, invoice_no: str, customer_id: float,
                   amount: float, reason: str) -> tuple[dict, bool]:
    """Open a pending return, or find the one this key already opened.

    Returns `(row, created)`. Nothing is paid here — that is `approve`'s job.
    """
    con = open_ledger(path)
    try:
        try:
            con.execute(
                "INSERT INTO returns (return_id, idempotency_key, invoice_no, customer_id, amount, "
                "reason, status, requested_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (f"R-{uuid.uuid4().hex[:8].upper()}", key, invoice_no, customer_id, amount,
                 reason[:500], _now()),
            )
            con.commit()
            created = True
        except sqlite3.IntegrityError:
            created = False
        row = con.execute("SELECT * FROM returns WHERE idempotency_key = ?", (key,)).fetchone()
        return dict(row), created
    finally:
        con.close()


class LedgerError(Exception):
    """A decision the ledger refused. `code` is what the API returns."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def approve(path: Path, return_id: str, staff: str) -> tuple[dict, bool]:
    """Pay a pending return. Returns `(refund, replayed)`.

    Approving something already approved is not an error: it is a retry, and the answer to
    a retry is the result of the first attempt. That is what makes the button safe to press
    twice and the request safe to resend after a timeout.
    """
    con = open_ledger(path)
    try:
        con.execute("BEGIN IMMEDIATE")          # one decision at a time on this file
        ret = con.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone()
        if ret is None:
            con.rollback()
            raise LedgerError("return_not_found", f"No return {return_id}.")
        if ret["status"] == "approved":
            refund = con.execute("SELECT * FROM refunds WHERE return_id = ?", (return_id,)).fetchone()
            con.rollback()
            return dict(refund), True
        if ret["status"] == "rejected":
            con.rollback()
            raise LedgerError("conflict", f"Return {return_id} was rejected; it cannot be paid.")
        try:
            con.execute(
                "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"F-{uuid.uuid4().hex[:8].upper()}", return_id, ret["invoice_no"],
                 ret["customer_id"], ret["amount"], staff, _now()),
            )
        except sqlite3.IntegrityError:
            con.rollback()
            raise LedgerError("conflict", f"Order {ret['invoice_no']} has already been refunded.")
        con.execute(
            "UPDATE returns SET status = 'approved', decided_by = ?, decided_at = ? WHERE return_id = ?",
            (staff, _now(), return_id),
        )
        con.commit()
        refund = con.execute("SELECT * FROM refunds WHERE return_id = ?", (return_id,)).fetchone()
        return dict(refund), False
    finally:
        con.close()


def reject(path: Path, return_id: str, staff: str) -> dict:
    con = open_ledger(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        ret = con.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone()
        if ret is None:
            con.rollback()
            raise LedgerError("return_not_found", f"No return {return_id}.")
        if ret["status"] == "approved":
            con.rollback()
            raise LedgerError("conflict", f"Return {return_id} has already been paid.")
        con.execute(
            "UPDATE returns SET status = 'rejected', decided_by = ?, decided_at = ? WHERE return_id = ?",
            (staff, _now(), return_id),
        )
        con.commit()
        return dict(con.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone())
    finally:
        con.close()


def list_returns(path: Path, status: str | None = None,
                 customer_id: float | None = None) -> list[dict]:
    """Return requests, oldest first, each with its refund if one was paid."""
    con = open_ledger(path)
    try:
        sql = ("SELECT r.*, f.refund_id, f.paid_at FROM returns r "
               "LEFT JOIN refunds f ON f.return_id = r.return_id WHERE 1 = 1")
        args: list = []
        if status:
            sql += " AND r.status = ?"
            args.append(status)
        if customer_id is not None:
            sql += " AND r.customer_id = ?"
            args.append(customer_id)
        rows = con.execute(sql + " ORDER BY r.requested_at", args).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def counts(path: Path) -> dict:
    """What is in the ledger right now — the thing to read after any test that spends."""
    con = open_ledger(path)
    try:
        by_status = dict(con.execute("SELECT status, COUNT(*) FROM returns GROUP BY status").fetchall())
        paid = con.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM refunds").fetchone()
        return {
            "pending": by_status.get("pending", 0),
            "approved": by_status.get("approved", 0),
            "rejected": by_status.get("rejected", 0),
            "refunds": paid[0],
            "refunded": round(paid[1], 2),
        }
    finally:
        con.close()
