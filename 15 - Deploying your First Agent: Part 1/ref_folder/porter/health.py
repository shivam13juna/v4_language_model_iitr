"""What the service depends on, checked — for /healthz, and for a container that has not
started a server yet.

    python -m porter.selfcheck

Four dependencies, and each one fails in a way the others do not:

    database   the orders file is mounted where PORTER_DB_PATH says, and readable
    model      there is a key if the endpoint needs one, and the endpoint answers /models
               and serves the model the settings name
    ledger     the /state volume is there and writable
    state      conversations can be stored: Postgres if a DSN is set, else a SQLite file
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from porter import ledger
from porter.settings import Settings
from porter.tools import read_only


async def check(settings: Settings) -> dict[str, str]:
    checks: dict[str, str] = {}

    try:
        con = read_only(settings.db_path)
        con.execute("SELECT 1 FROM invoices LIMIT 1").fetchone()
        con.close()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - the reason is the useful part
        checks["database"] = f"failed: {type(exc).__name__}: {exc}"[:120]

    if settings.is_scripted:
        checks["model"] = "ok"
    elif not settings.api_key:
        # Only OpenAI is ever left without a key (settings.py). Sent empty, the header is not
        # even valid HTTP, and the failure would read like a network fault.
        checks["model"] = "no API key: set OPENAI_API_KEY"
    else:
        # /models is part of the OpenAI wire format: the same check works against Ollama, a
        # tunnel in front of Ollama, or a hosted provider.
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{settings.base_url.rstrip('/')}/models",
                                     headers={"Authorization": f"Bearer {settings.api_key}"})
            if r.status_code != 200:
                checks["model"] = f"http {r.status_code} from {settings.base_url}"
            else:
                names = {m.get("id") for m in r.json().get("data", [])}
                checks["model"] = "ok" if settings.model in names else f"{settings.model} is not served there"
        except Exception as exc:  # noqa: BLE001
            checks["model"] = f"unreachable: {type(exc).__name__} at {settings.base_url}"

    try:
        ledger.open_ledger(settings.ledger_path).close()
        checks["ledger"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["ledger"] = f"failed: {type(exc).__name__}: {exc}"[:120]

    if settings.postgres_dsn:
        try:
            import psycopg

            with psycopg.connect(settings.postgres_dsn, connect_timeout=3) as pg:
                pg.execute("SELECT 1")
            checks["state"] = "ok (postgres)"
        except Exception as exc:  # noqa: BLE001
            checks["state"] = f"failed: {type(exc).__name__}"
    else:
        try:
            Path(settings.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
            sqlite3.connect(settings.checkpoint_path).close()
            checks["state"] = "ok (sqlite)"
        except Exception as exc:  # noqa: BLE001
            checks["state"] = f"failed: {type(exc).__name__}"
    return checks


def healthy(checks: dict[str, str]) -> bool:
    return all(v.startswith("ok") for v in checks.values())
