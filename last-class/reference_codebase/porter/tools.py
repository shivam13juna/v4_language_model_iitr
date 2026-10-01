"""Porter's three tools, built for one customer: two read the orders, start_return writes a request
to the ledger."""

import sqlite3
import time
import uuid

from langchain_core.tools import tool

from porter.ledger import request_return


def read_only(db_path):
    """A connection SQLite itself refuses to write through."""
    # mode=ro opens the file read-only. A plain sqlite3.connect(path) would not do: a DROP TABLE
    # sent through it would be committed.
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)

# Said in every lookup, so the model has no gap to fill with an invented delivery date.
NO_TRACKING = "This desk has no shipping, dispatch or delivery information for any order."

def build_tools(customer_id, db_path, ledger_path, idempotency_key=None, deadline=None):
    """The three tools, bound to one customer and one request."""
    # Everything the tools need is an argument of build_tools(), passed in by the code handling
    # the request. The tools below use these variables directly: they are closures.

    @tool
    def look_up_order(invoice_no: str) -> str:
        """Look up one of this customer's orders: when it was placed, how many lines it has,
        and whether it was cancelled. It cannot say where an order is — there is no shipping
        or delivery information. Use the order number exactly as the customer wrote it."""
        con = read_only(db_path)
        # One row per order: its number, when it was placed, whether it was cancelled, and how many
        # lines it has. "AND i.customer_id = ?" means only this customer's orders are found.
        row = con.execute("""
            SELECT i.invoice_no, i.invoice_ts, i.is_cancelled, COUNT(li.stock_code)
            FROM invoices i JOIN line_items li ON li.invoice_no = i.invoice_no
            WHERE i.invoice_no = ? AND i.customer_id = ?
            GROUP BY i.invoice_no""", (invoice_no.strip(), customer_id)).fetchone()
        con.close()
        if row is None:
            # Not found and not theirs get the same reply.
            return f"No order {invoice_no!r} on this account."
        no, placed, cancelled, lines = row
        return f"Order {no}: placed on {placed}, {lines} line(s), {'cancelled' if cancelled else 'not cancelled'}. {NO_TRACKING}"

    @tool
    def price_order(invoice_no: str) -> str:
        """Itemise one of this customer's orders and give its total. Use this for any question
        about what something cost — never work the total out yourself."""
        con = read_only(db_path)
        # Is the order this customer's?
        owned = con.execute("SELECT 1 FROM invoices WHERE invoice_no = ? AND customer_id = ?",
                            (invoice_no.strip(), customer_id)).fetchone()
        # Its lines with product names, most expensive first. LEFT JOIN keeps a line whose
        # product has no name.
        rows = con.execute("""
            SELECT p.description, li.quantity, li.unit_price
            FROM line_items li LEFT JOIN products p ON p.stock_code = li.stock_code
            WHERE li.invoice_no = ? ORDER BY li.quantity * li.unit_price DESC""",
            (invoice_no.strip(),)).fetchall()
        con.close()
        if owned is None:
            return f"No order {invoice_no!r} on this account."
        # The total is added up here, in Python, so the model never does arithmetic.
        total = round(sum(quantity * price for _, quantity, price in rows), 2)
        # At most twelve lines; a longer order ends with "... and N more line(s)".
        items = [f"  {q} x {(d or 'unknown item').title()} @ {p:.2f}" for d, q, p in rows[:12]]
        if len(rows) > 12:
            items.append(f"  ... and {len(rows) - 12} more line(s)")
        return f"Order {invoice_no} — {len(rows)} line(s), total {total:.2f}\n" + "\n".join(items)

    @tool
    def start_return(invoice_no: str, reason: str) -> str:
        """Open a return request for one of this customer's orders. Nothing is refunded until a
        member of staff approves it. The amount comes from the order, never from the customer's
        message. Call this only once the customer has clearly asked to send something back."""
        # 1. If the caller has already given up, don't start a write nobody will hear about.
        if deadline is not None and time.monotonic() > deadline:      # the caller has given up
            return "Out of time for this request, so no return was opened."
        # 2. The amount is the order's total, read from the database: never a number the customer typed.
        con = read_only(db_path)
        amount, cancelled = con.execute("""
            SELECT ROUND(SUM(li.quantity * li.unit_price), 2), i.is_cancelled
            FROM invoices i JOIN line_items li ON li.invoice_no = i.invoice_no
            WHERE i.invoice_no = ? AND i.customer_id = ?""", (invoice_no.strip(), customer_id)).fetchone()
        con.close()
        if amount is None:
            return f"No order {invoice_no!r} on this account, so there is nothing to return."
        if cancelled:
            return f"Order {invoice_no} was already cancelled; nothing to return."
        # 3. Record the request in the ledger. Without a key from the caller, every call gets a new
        #    random one, so a repeated request opens a second return.
        request, created = request_return(ledger_path, key=idempotency_key or f"auto:{uuid.uuid4()}",
                                          invoice_no=invoice_no.strip(), customer_id=customer_id,
                                          amount=amount, reason=reason)
        if not created:                                                # the same request again
            return (f"Return request {request['return_id']} is already open on order {invoice_no}; "
                    "this was a repeat, so nothing new was opened.")
        return (f"Return request {request['return_id']} opened on order {invoice_no} for {amount:.2f}. "
                "A colleague has to approve it before anything is refunded.")

    return [look_up_order, price_order, start_return]
