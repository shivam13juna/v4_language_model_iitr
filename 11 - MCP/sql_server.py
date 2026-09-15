"""
sql_server.py — a small MCP server exposing three read-only SQLite tools over HTTP.

Run it yourself in a terminal (just like math_server.py / text_server.py):

    python sql_server.py        # serves MCP at http://127.0.0.1:8005/mcp

On first run it creates a tiny sample database (data/sample.db) next to this file,
then serves the tools. The companion notebook (v3_mcp_demo_sql_simplified.ipynb)
connects to this running server and lets an LLM use the tools.

Tools only — no resources, no prompts.
"""

import re
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# The database lives next to this file at ./data/sample.db
DB_PATH = Path(__file__).parent / "data" / "sample.db"

# FastMCP takes host/port for HTTP transports.
mcp = FastMCP("Text2SQL", host="127.0.0.1", port=8005)


def _get_connection() -> sqlite3.Connection:
    """Open a fresh SQLite connection."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}.")
    return sqlite3.connect(str(DB_PATH))


@mcp.tool()
def list_tables() -> list[str]:
    """Get all table names in the database. Call this FIRST to discover what tables exist before writing any query."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


@mcp.tool()
def get_table_schema(table_name: str) -> list[dict]:
    """Get the column names and types for one table. Use this after list_tables, before writing SQL, so you only reference columns that really exist."""
    # The table name comes from the LLM — validate it before putting it in a query.
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")

    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info(`{table_name}`);")
        rows = cur.fetchall()
        if not rows:
            raise ValueError(f"Table '{table_name}' not found in the database.")
        return [
            {"name": r[1], "type": r[2], "nullable": not r[3], "primary_key": bool(r[5])}
            for r in rows
        ]
    finally:
        conn.close()


@mcp.tool()
def execute_sql(query: str) -> str:
    """Run a read-only SQL SELECT query and return the matching rows as text. ONLY SELECT is allowed — no INSERT, UPDATE, DELETE, DROP, or ALTER."""
    # Safety: refuse anything that could change the data.
    normalized = query.strip().upper()
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "REPLACE"]
    for keyword in forbidden:
        if normalized.startswith(keyword) or f" {keyword} " in f" {normalized} ":
            raise ValueError(f"Only SELECT queries are allowed (found '{keyword}').")

    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if not rows:
            return "(no rows returned)"
        header = " | ".join(columns)
        separator = "-+-".join("-" * max(len(c), 8) for c in columns)
        body = "\n".join(" | ".join(str(v) for v in row) for row in rows)
        return f"{header}\n{separator}\n{body}"
    except sqlite3.Error as e:
        # Hand the error back as text so the agent can read it and try again.
        return f"SQL Error: {e}"
    finally:
        conn.close()


def _ensure_database() -> None:
    """Create a small sample database on first run (employees / customers / orders)."""
    if DB_PATH.exists():
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("""CREATE TABLE employees (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
        salary REAL NOT NULL, city TEXT NOT NULL);""")
    cur.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?)", [
        (1, "Alice", "Engineering",  95000, "Berlin"),
        (2, "Bob",   "Engineering",  88000, "Berlin"),
        (3, "Carol", "Marketing",    72000, "Prague"),
        (4, "David", "Marketing",    68000, "Prague"),
        (5, "Eve",   "Engineering", 102000, "London"),
        (6, "Frank", "Sales",        61000, "London"),
        (7, "Grace", "Sales",        59000, "Berlin"),
        (8, "Heidi", "Engineering",  97000, "Prague"),
    ])

    cur.execute("""CREATE TABLE customers (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL,
        currency TEXT NOT NULL, balance REAL NOT NULL);""")
    cur.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", [
        (1,  "Acme GmbH",        "Germany",        "EUR",  15400.50),
        (2,  "Prague Widgets",   "Czech Republic", "CZK", 342000.00),
        (3,  "Berlin Tech",      "Germany",        "EUR",  88200.00),
        (4,  "London Analytics", "UK",             "GBP",  45000.00),
        (5,  "CZ Solutions",     "Czech Republic", "CZK", 198000.00),
        (6,  "Euro Supplies",    "France",         "EUR",  23100.75),
        (7,  "Nordic Data",      "Sweden",         "SEK",  67000.00),
        (8,  "Praha Services",   "Czech Republic", "CZK", 410000.00),
        (9,  "Milan Corp",       "Italy",          "EUR",  54200.00),
        (10, "Swiss Holdings",   "Switzerland",    "CHF", 120000.00),
    ])

    cur.execute("""CREATE TABLE orders (
        id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, product TEXT NOT NULL,
        amount REAL NOT NULL, order_date TEXT NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers(id));""")
    cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", [
        (1,  1, "Widget A",    2500.00, "2024-11-15"),
        (2,  1, "Widget B",    1800.00, "2025-01-10"),
        (3,  2, "Gadget X",    5400.00, "2025-02-01"),
        (4,  3, "Widget A",    3200.00, "2025-01-20"),
        (5,  4, "Service Pro", 9000.00, "2025-03-05"),
        (6,  5, "Gadget X",    2700.00, "2024-12-22"),
        (7,  6, "Widget B",    1100.00, "2025-02-14"),
        (8,  9, "Service Pro", 6500.00, "2025-01-30"),
        (9,  3, "Gadget Y",    4100.00, "2025-03-12"),
        (10, 7, "Widget A",    1900.00, "2025-02-28"),
    ])

    conn.commit()
    conn.close()


if __name__ == "__main__":
    _ensure_database()
    print(f"Sample database ready at {DB_PATH}")
    print("Serving MCP at http://127.0.0.1:8005/mcp   (press Ctrl-C to stop)")
    mcp.run(transport="streamable-http")
