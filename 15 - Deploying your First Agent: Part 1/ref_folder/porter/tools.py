"""Porter's three tools, built fresh for each caller.

Two of them read. One of them writes. The difference shows up in four places:

  * the reading tools open the database `mode=ro`, so a confused agent physically cannot
    write through them;
  * the writing tool opens a return *request* and nothing more — paying it is a separate
    action, taken by a member of staff, through a route the agent has no tool for;
  * that request carries an idempotency key, so the same request sent twice opens one
    return, and a deadline, so a request the caller has already given up on opens none;
  * all three are built inside `build_tools(customer_id)`, so the caller's identity is
    closed over rather than passed in by the model. The model never gets to choose whose
    orders it is looking at.

What the data does not contain matters as much as what it does. `invoices.country` is the
country on the customer's account, not a destination, and there is no shipping, dispatch or
delivery field anywhere in the database — so the lookup says so, rather than letting the
model fill the gap.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from langchain_core.tools import tool

from porter import ledger
from porter.obs import RETURNS


def read_only(db_path: Path) -> sqlite3.Connection:
    """A connection SQLite itself refuses to write through.

    `sqlite3.connect(path)` would not do: DDL autocommits under Python's legacy
    transaction handling, so a `DROP TABLE` reaching this connection would persist.
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)


NO_TRACKING = "This desk has no shipping, dispatch or delivery information for any order."


def customer_orders(db_path: Path, customer_id: float) -> list[dict]:
    """Every order on one customer's account, newest first, for the order desk's list.

    Not a tool: the model never sees it. It reads the same way the tools do, through a
    read-only connection and only for the customer it is given.
    """
    con = read_only(db_path)
    try:
        rows = con.execute(
            """
            SELECT i.invoice_no, i.invoice_ts, i.is_cancelled, COUNT(li.stock_code),
                   ROUND(SUM(li.quantity * li.unit_price), 2)
            FROM invoices i
            JOIN line_items li ON li.invoice_no = i.invoice_no
            WHERE i.customer_id = ?
            GROUP BY i.invoice_no
            ORDER BY i.invoice_ts DESC
            """,
            (customer_id,),
        ).fetchall()
    finally:
        con.close()
    return [{"invoice_no": number, "placed_at": placed, "cancelled": bool(cancelled),
             "lines": lines, "total": total}
            for number, placed, cancelled, lines, total in rows]


def build_tools(customer_id: float, db_path: Path, ledger_path: Path,
                idempotency_key: str | None = None, deadline: float | None = None):
    """Return the three tools, bound to one customer and one request.

    `customer_id` is closed over, not a tool argument — the model cannot ask for someone
    else's order because there is no parameter in which to name them. `deadline` is a
    `time.monotonic()` value: past it, the writing tool refuses to start.
    """

    @tool
    def look_up_order(invoice_no: str) -> str:
        """Look up one of this customer's orders: when it was placed, how many lines it has,
        and whether it was cancelled. It cannot say where an order is — there is no
        shipping or delivery information. Use the order number exactly as the customer
        wrote it."""
        con = read_only(db_path)
        try:
            row = con.execute(
                """
                SELECT i.invoice_no, i.invoice_ts, i.is_cancelled, COUNT(li.stock_code)
                FROM invoices i
                JOIN line_items li ON li.invoice_no = i.invoice_no
                WHERE i.invoice_no = ? AND i.customer_id = ?
                GROUP BY i.invoice_no
                """,
                (invoice_no.strip(), customer_id),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            return f"No order {invoice_no!r} on this account."
        no, ts, cancelled, lines = row
        state = "cancelled" if cancelled else "not cancelled"
        return f"Order {no}: placed on {ts}, {lines} line(s), {state}. {NO_TRACKING}"

    @tool
    def price_order(invoice_no: str) -> str:
        """Itemise one of this customer's orders and give its total. Use this for any
        question about what something cost — never work the total out yourself."""
        con = read_only(db_path)
        try:
            owned = con.execute(
                "SELECT 1 FROM invoices WHERE invoice_no = ? AND customer_id = ?",
                (invoice_no.strip(), customer_id),
            ).fetchone()
            if owned is None:
                return f"No order {invoice_no!r} on this account."
            rows = con.execute(
                """
                SELECT p.description, li.quantity, li.unit_price
                FROM line_items li
                LEFT JOIN products p ON p.stock_code = li.stock_code
                WHERE li.invoice_no = ?
                ORDER BY li.quantity * li.unit_price DESC
                """,
                (invoice_no.strip(),),
            ).fetchall()
        finally:
            con.close()

        total = round(sum(q * pr for _, q, pr in rows), 2)
        lines = [f"  {q} x {(d or 'unknown item').title()} @ {pr:.2f}" for d, q, pr in rows[:12]]
        if len(rows) > 12:
            lines.append(f"  ... and {len(rows) - 12} more line(s)")
        return f"Order {invoice_no} — {len(rows)} line(s), total {total:.2f}\n" + "\n".join(lines)

    @tool
    def start_return(invoice_no: str, reason: str) -> str:
        """Open a return request for one of this customer's orders. Nothing is refunded
        until a member of staff approves it. The amount comes from the order, never from
        the customer's message. Call this only once the customer has clearly asked to send
        something back."""
        # The caller has already been told this request failed. Opening a return now would
        # be work nobody is waiting for, on a request that will probably be retried.
        if deadline is not None and time.monotonic() > deadline:
            return "Out of time for this request, so no return was opened."

        con = read_only(db_path)
        try:
            row = con.execute(
                """
                SELECT ROUND(SUM(li.quantity * li.unit_price), 2), i.is_cancelled
                FROM invoices i
                JOIN line_items li ON li.invoice_no = i.invoice_no
                WHERE i.invoice_no = ? AND i.customer_id = ?
                """,
                (invoice_no.strip(), customer_id),
            ).fetchone()
        finally:
            con.close()

        if row is None or row[0] is None:
            return f"No order {invoice_no!r} on this account, so there is nothing to return."
        amount, cancelled = row
        if cancelled:
            return f"Order {invoice_no} was already cancelled; nothing to return."

        request, created = ledger.request_return(
            ledger_path,
            key=idempotency_key or f"auto:{uuid.uuid4()}",
            invoice_no=invoice_no.strip(),
            customer_id=customer_id,
            amount=amount,
            reason=reason,
        )
        RETURNS.labels(outcome="requested" if created else "repeat").inc()
        if not created:
            return (
                f"Return request {request['return_id']} for {request['amount']:.2f} is already open "
                f"on order {request['invoice_no']} — this was a repeat, so nothing new was opened."
            )
        return (
            f"Return request {request['return_id']} opened on order {invoice_no} for {amount:.2f}. "
            "A colleague has to approve it before anything is refunded."
        )

    return [look_up_order, price_order, start_return]
