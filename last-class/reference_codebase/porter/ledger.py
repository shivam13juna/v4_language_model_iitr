"""The ledger: a small SQLite file, apart from the shop's orders, holding the one thing the agent may
change. start_return adds a return request to it; a member of staff approves the request, and only
then is a refund written."""

import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException


def open_ledger(path):
    """Open the ledger file, creating it and its two tables the first time."""
    con = sqlite3.connect(path, timeout=10)      # if another connection is writing, wait up to 10 s
    con.row_factory = sqlite3.Row                # rows can be read by column name: row["status"]
    # Two tables:
    #   returns — one row per return request the desk opened; status goes from pending to approved
    #   refunds — one row per refund a member of staff approved. No tool writes here.
    # IF NOT EXISTS makes this safe to run on every open: it only creates what is missing.
    con.executescript("""
        CREATE TABLE IF NOT EXISTS returns (
            return_id        TEXT PRIMARY KEY,
            idempotency_key  TEXT UNIQUE NOT NULL,     -- the same request twice opens one return
            invoice_no       TEXT NOT NULL,
            customer_id      REAL NOT NULL,
            amount           REAL NOT NULL,
            reason           TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            requested_at     TEXT NOT NULL,
            decided_by       TEXT,
            decided_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS refunds (
            refund_id    TEXT PRIMARY KEY,
            return_id    TEXT UNIQUE NOT NULL,         -- the same approval twice pays once
            invoice_no   TEXT UNIQUE NOT NULL,         -- an order is refunded at most once
            customer_id  REAL NOT NULL,
            amount       REAL NOT NULL,
            approved_by  TEXT NOT NULL,
            paid_at      TEXT NOT NULL
        );
    """)
    return con


def now():
    """The time now, in UTC, as text: 2026-09-28T10:15:00+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_return(path, *, key, invoice_no, customer_id, amount, reason):
    """Open a pending return, or find the one this key already opened. Returns (row, created)."""
    con = open_ledger(path)
    try:
        # Add a new request, with a random id such as R-3F2A9C1B and the status 'pending'.
        con.execute("INSERT INTO returns (return_id, idempotency_key, invoice_no, customer_id, amount, reason, requested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"R-{uuid.uuid4().hex[:8].upper()}", key, invoice_no, customer_id, amount, reason[:500], now()))
        con.commit()
        created = True
    except sqlite3.IntegrityError:                     # this key already opened one
        # UNIQUE (idempotency_key) refused the row: this exact request was made before.
        # Nothing new is written; the row the first attempt created is returned below.
        created = False
    # Either way, read back the row that belongs to this key.
    row = con.execute("SELECT * FROM returns WHERE idempotency_key = ?", (key,)).fetchone()
    con.close()
    return dict(row), created


def list_returns(path):
    """Every return request, oldest first, with its refund if it has been paid: what staff decide on."""
    con = open_ledger(path)
    rows = con.execute("SELECT r.*, f.refund_id, f.paid_at FROM returns r "
                       "LEFT JOIN refunds f ON f.return_id = r.return_id ORDER BY r.requested_at").fetchall()
    con.close()
    return [dict(r) for r in rows]


def approve(path, return_id, staff):
    """Pay a pending return. Returns (refund, replayed): approving it again returns the first refund."""

    # 1. Lock / begin transaction
    # 2. Find pending return
    # 3. Check whether already approved
    # 4. Create refund
    # 5. Mark return approved
    # 6. Commit everything together
    con = open_ledger(path)
    try:
        con.execute("BEGIN IMMEDIATE")                        # one decision at a time on this file
        ret = con.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone()
        if ret is None:
            raise HTTPException(404, f"No return {return_id}.")
        if ret["status"] == "approved":                       # a retry: answer with the first result
            return dict(con.execute("SELECT * FROM refunds WHERE return_id = ?", (return_id,)).fetchone()), True
        # Pay it: one row in refunds, with a random id such as F-7C1D22E0.
        try:
            con.execute("INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (f"F-{uuid.uuid4().hex[:8].upper()}", return_id, ret["invoice_no"],
                         ret["customer_id"], ret["amount"], staff, now()))
        except sqlite3.IntegrityError:                        # UNIQUE (invoice_no)
            # A different return on the same order has already been paid.
            raise HTTPException(409, f"Order {ret['invoice_no']} has already been refunded.")
        # Mark the request approved, and by whom. The refund and this change are saved together, at commit.
        con.execute("UPDATE returns SET status = 'approved', decided_by = ?, decided_at = ? WHERE return_id = ?",
                    (staff, now(), return_id))
        con.commit()
        return dict(con.execute("SELECT * FROM refunds WHERE return_id = ?", (return_id,)).fetchone()), False
    finally:
        con.close()                                           # closing without a commit undoes anything half-done
