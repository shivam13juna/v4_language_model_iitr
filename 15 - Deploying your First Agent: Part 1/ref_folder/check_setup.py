#!/usr/bin/env python
"""Is this machine ready? Checks everything this folder's notebook needs, and says how to fix what is not.

    python check_setup.py            # everything
    python check_setup.py --quick    # skip the model's tool-calling probe (it takes ~10s)

✅ ready   ❌ needed and missing   ⚠️  only needed for one part (named on the line)
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
rows: list[tuple[str, str, str]] = []


def ok(name: str, detail: str = "") -> None:
    rows.append(("✅", name, detail))


def bad(name: str, fix: str) -> None:
    rows.append(("❌", name, fix))


def warn(name: str, fix: str) -> None:
    rows.append(("⚠️ ", name, fix))


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        ok("python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        bad("python", f"{v.major}.{v.minor} found; 3.11 or newer is needed (3.12 is what this was built on)")


def check_packages() -> None:
    wanted: dict[str, str] = {}
    for req in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / req).read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s#]+)", line.strip())
            if m:
                wanted[re.sub(r"\[.*\]", "", m.group(1))] = m.group(2)
    missing, different = [], []
    for name, version in wanted.items():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if have != version:
            different.append(f"{name} {have}≠{version}")
    if missing:
        bad("packages", f"missing {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''} — "
                        "pip install -r requirements-dev.txt")
    elif different:
        warn("packages", f"versions differ from the pins: {', '.join(different[:4])} — "
                         "pip install -r requirements-dev.txt")
    else:
        ok("packages", f"{len(wanted)} pinned, all match")


def check_data() -> None:
    db = ROOT / "online_retail.db"
    if not db.exists():
        bad("orders database", "python prepare_data.py   (downloads UCI Online Retail, ~23 MB)")
        return
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("invoices", "line_items", "products")}
    con.close()
    if counts == {"invoices": 25_900, "line_items": 541_909, "products": 3_958}:
        ok("orders database", "25,900 invoices · 541,909 lines · 3,958 products")
    else:
        bad("orders database", f"unexpected row counts {counts} — python prepare_data.py --rebuild")
    for name in ("evals/smoke.jsonl",):
        if not (ROOT / name).exists():
            bad(name, "missing — it ships with the folder; restore it")


def check_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        bad(".env", "cp .env.example .env   and set PORTER_AUTH_SECRET")
        return
    values = dict(line.split("=", 1) for line in env.read_text().splitlines()
                  if "=" in line and not line.lstrip().startswith("#"))
    secret = values.get("PORTER_AUTH_SECRET", "").strip()
    if len(secret) >= 32:
        ok(".env", "PORTER_AUTH_SECRET set")
    else:
        bad(".env", "PORTER_AUTH_SECRET needs 32+ characters: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(48))\"")


def check_model(quick: bool) -> None:
    import httpx

    sys.path.insert(0, str(ROOT))
    from porter.settings import PROVIDERS, get_settings

    s = get_settings()
    # Judge by where requests actually go, so the advice fits even a half-switched .env.
    on_openai = s.base_url.startswith(PROVIDERS["openai"]["base_url"])
    if s.provider == "openai" and not on_openai:
        warn("provider", f"PORTER_PROVIDER is openai, but requests go to {s.base_url}. If that is left over "
                         "from Ollama, delete PORTER_MODEL, PORTER_BASE_URL and PORTER_API_KEY from .env")
    if not s.api_key:
        bad("model endpoint", "no OPENAI_API_KEY — put it in .env, or set PORTER_PROVIDER=ollama to use Ollama")
        return
    headers = {"Authorization": f"Bearer {s.api_key}"}
    try:
        r = httpx.get(f"{s.base_url.rstrip('/')}/models", headers=headers, timeout=5)
    except Exception as exc:  # noqa: BLE001
        fix = ("check this machine's internet connection" if on_openai else
               "start Ollama (ollama serve), or point PORTER_BASE_URL at the shared tunnel")
        bad("model endpoint", f"{s.base_url} unreachable ({type(exc).__name__}) — {fix}")
        return
    if r.status_code != 200:
        which = "OPENAI_API_KEY" if on_openai else "PORTER_API_KEY (the tunnel's token)"
        bad("model endpoint", f"{s.base_url} answered {r.status_code} — check {which}")
        return
    served = {m.get("id") for m in r.json().get("data", [])}
    if s.model not in served:
        fix = "this key cannot use it; set PORTER_MODEL to one it can" if on_openai else f"ollama pull {s.model}"
        bad("model", f"{s.model} is not served at {s.base_url} — {fix}")
        return
    ok("model endpoint", f"{s.model} at {s.base_url}")
    if quick:
        return
    # Tool calling is the one capability Porter cannot do without; several small models lack it.
    # No temperature here: gpt-5 and o-series models refuse anything but their default.
    probe = {
        "model": s.model,
        "messages": [{"role": "user", "content": "What is order 580638's total? Use the tool."}],
        "tools": [{"type": "function", "function": {
            "name": "price_order", "description": "Price one order.",
            "parameters": {"type": "object",
                           "properties": {"invoice_no": {"type": "string"}},
                           "required": ["invoice_no"]}}}],
    }
    try:
        r = httpx.post(f"{s.base_url.rstrip('/')}/chat/completions", headers=headers, json=probe, timeout=180)
        calls = r.json()["choices"][0]["message"].get("tool_calls") or []
        if calls and calls[0]["function"]["name"] == "price_order":
            ok("tool calling", f"{s.model} called price_order({json.loads(calls[0]['function']['arguments'])})")
        else:
            bad("tool calling", f"{s.model} answered without calling the tool — use gpt-4.1-mini, "
                                "or gpt-oss:20b or llama3.1:8b on Ollama")
    except Exception as exc:  # noqa: BLE001
        bad("tool calling", f"probe failed: {type(exc).__name__}: {str(exc)[:80]}")


def check_docker() -> None:
    if not shutil.which("docker"):
        warn("docker", "not installed — needed for the container and deployment parts")
        return
    info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
    if info.returncode != 0:
        warn("docker", "installed, but the daemon is not running — start Docker Desktop")
        return
    compose = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    ok("docker", f"engine {info.stdout.strip()} · compose {compose.stdout.strip() or '?'}")


def check_ports() -> None:
    busy = []
    for port, what in ((8080, "porter"),):
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(f"{port} ({what})")
    if busy:
        warn("ports", f"already in use: {', '.join(busy)} — fine if it is this stack; otherwise free them")
    else:
        ok("ports", "8080 free")


def check_deploy_tools() -> None:
    if shutil.which("ngrok"):
        ok("ngrok", "installed — for the model tunnel, used only with PORTER_PROVIDER=ollama")
    else:
        warn("ngrok", "not installed — only a machine serving its Ollama through the tunnel needs it "
                      "(brew install ngrok)")
    if not shutil.which("aws"):
        warn("aws cli", "not installed — only needed to deploy to EC2 yourself")
        return
    who = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
                         capture_output=True, text=True, timeout=20)
    if who.returncode == 0:
        ok("aws credentials", who.stdout.strip())
    else:
        warn("aws credentials", "not working — aws configure (only needed to deploy to EC2 yourself)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    for step in (check_python, check_packages, check_data, check_env,
                 lambda: check_model(args.quick), check_docker, check_ports, check_deploy_tools):
        step()
    width = max(len(name) for _, name, _ in rows)
    for mark, name, detail in rows:
        print(f"{mark} {name:{width}s}  {detail}")
    failed = sum(1 for mark, _, _ in rows if mark == "❌")
    print(f"\n{'ready' if not failed else f'{failed} thing(s) to fix before starting'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
