"""Check a fresh environment without starting a server.

    python -m porter.selfcheck
    docker run --rm porter:2.0.0 python -m porter.selfcheck

It imports the whole app (so a missing package fails here, not at 3am), reads the settings
the way the server would, and checks every dependency the server needs. Exit code 0 means
the same process started as a server would report healthy.
"""

from __future__ import annotations

import asyncio
import json
import sys

import porter.api  # noqa: F401 - importing the app is part of the check
from porter.health import check, healthy
from porter.settings import DEV_SECRET, get_settings


def main() -> int:
    settings = get_settings()
    checks = asyncio.run(check(settings))
    if settings.auth_required and settings.auth_secret == DEV_SECRET:
        checks["auth secret"] = "warning: the development secret — set PORTER_AUTH_SECRET"
    print(json.dumps({"version": settings.version, "model": settings.model,
                      "base_url": settings.base_url, "checks": checks}, indent=2))
    return 0 if healthy({k: v for k, v in checks.items() if k != "auth secret"}) else 1


if __name__ == "__main__":
    sys.exit(main())
