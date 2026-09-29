"""The gate: cases run against the service over HTTP, scored the same way every time.

What the suite talks to matters more than which metric it uses. Testing `answer()` says the
agent works; testing the HTTP contract says the *service* works — the sign-in, the screens,
the routing, the prompt version actually deployed, the middleware somebody added on Friday.

Three suites, three jobs (see evals/build_golden.py):

    smoke     8 cases, under a minute — every deploy, against the candidate
    golden    39 cases — the regression suite prompts are tuned against
    heldout   24 cases — looked at only once a change is final

    python -m porter.evals --suite smoke --in-process           # inside an image, no server
    python -m porter.evals --suite golden --url http://127.0.0.1:8080
    python -m porter.evals --suite heldout --save results/heldout_v3.json

Against a URL, the runner signs a token per case with PORTER_AUTH_SECRET — test-harness
privilege, the same secret the service checks with.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

EVALS = Path(__file__).resolve().parent.parent / "evals"
BEHAVIOUR_KINDS = {"scope", "not_theirs", "no_action", "injection", "leak", "cancelled",
                   "ambiguous", "invented_total", "shipping"}


def load_cases(suite: str | Path = "golden") -> list[dict]:
    path = Path(suite) if str(suite).endswith(".jsonl") else EVALS / f"{suite}.jsonl"
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")
WRITTEN_DATE = re.compile(r"\b(?:(\d{1,2})(?:st|nd|rd|th)? (" + "|".join(MONTHS) + r")|("
                          + "|".join(MONTHS) + r") (\d{1,2})(?:st|nd|rd|th)?),? (\d{4})\b", re.I)


def _iso(match: re.Match) -> str:
    day, month = match[1] or match[4], match[2] or match[3]
    return f"{match[5]}-{MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"


def normalise(text: str) -> str:
    """Make a reply comparable without making it unrecognisable: models put narrow no-break
    spaces inside numbers and non-breaking hyphens inside dates, and a scorer that does not
    know marks £147.01 wrong because of a U+202F. For the same reason a date written out
    ("9 December 2010") becomes the ISO form the cases expect."""
    text = unicodedata.normalize("NFKC", text)
    for char in (" ", " ", " "):
        text = text.replace(char, " ")
    for char in ("‐", "‑", "‒", "–", "—"):
        text = text.replace(char, "-")
    text = text.replace("’", "'")
    return WRITTEN_DATE.sub(_iso, text.replace(",", ""))


@dataclass
class Result:
    id: str
    kind: str
    passed: bool
    reason: str = ""
    seconds: float = 0.0
    tokens: int = 0
    reply: str = ""
    tools: list[str] = field(default_factory=list)
    status: int = 200                 # the HTTP status; 0 when the call itself failed

    @property
    def answered(self) -> bool:
        """Did the service reply at all? A 504 has an empty reply, and an empty reply passes
        every "must not say X" check — so a run that was never answered is never rescored."""
        return self.status == 200 and not self.reason.startswith("http ")


def score(case: dict, reply: str, tools: list[str]) -> tuple[bool, str]:
    """One case, one verdict, and the reason in words a person can act on.

    Every kind of expectation is ALL-must-hold except `contains`, which is one-of (it lists
    the ways a model may phrase the same admission). A reply that gets half of a
    `contains_all` right fails.
    """
    expect = case.get("expect", {})
    text = normalise(reply)
    low = text.lower()

    for needle in expect.get("contains_all", []):
        if normalise(str(needle)).lower() not in low:
            return False, f"missing {needle!r}"
    any_of = expect.get("contains", [])
    if any_of and not any(normalise(str(n)).lower() in low for n in any_of):
        return False, f"none of {any_of[:3]}…" if len(any_of) > 3 else f"none of {any_of}"
    for needle in expect.get("not_contains", []):
        if normalise(str(needle)).lower() in low:
            return False, f"said {needle!r}"
    for pattern in expect.get("not_matches", []):
        if re.search(pattern, text, re.I):
            return False, f"claimed {pattern!r}"
    for name in expect.get("no_tools", []):
        if name in tools:
            return False, f"called {name}"
    return True, ""


@dataclass
class Report:
    """Scored by *case*, not by run: a case asked three times and failed once is a failed
    case. Averaging it to 67% would be the mean of a coin toss about whether a refund happens."""

    results: list[Result]
    wall_seconds: float = 0.0
    suite: str = ""
    label: str = ""

    def by_case(self) -> dict[str, list[Result]]:
        out: dict[str, list[Result]] = {}
        for r in self.results:
            out.setdefault(r.id, []).append(r)
        return out

    @property
    def passed(self) -> int:
        return sum(1 for runs in self.by_case().values() if all(r.passed for r in runs))

    @property
    def total(self) -> int:
        return len(self.by_case())

    @property
    def score(self) -> float:
        return self.passed / self.total if self.results else 0.0

    @property
    def tokens(self) -> int:
        return sum(r.tokens for r in self.results)

    def latency(self, q: float) -> float:
        times = sorted(r.seconds for r in self.results)
        return times[min(len(times) - 1, int(q * len(times)))] if times else 0.0

    def behaviour_failures(self) -> list[str]:
        return [cid for cid, runs in self.by_case().items()
                if runs[0].kind in BEHAVIOUR_KINDS and not all(r.passed for r in runs)]

    def by_kind(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for runs in self.by_case().values():
            bucket = out.setdefault(runs[0].kind, [0, 0])
            bucket[0] += int(all(r.passed for r in runs))
            bucket[1] += 1
        return {k: (v[0], v[1]) for k, v in sorted(out.items())}

    def failures(self) -> list[Result]:
        """One row per failed case: the first failing run, and how often it failed."""
        out = []
        for runs in self.by_case().values():
            bad = [r for r in runs if not r.passed]
            if bad:
                first = bad[0]
                out.append(Result(first.id, first.kind, False, f"{first.reason} ({len(bad)} of {len(runs)})",
                                  first.seconds, first.tokens, first.reply, first.tools, first.status))
        return out

    def table(self) -> str:
        lines = [f"{'kind':16s} {'passed':>8s}"]
        for kind, (passed, total) in self.by_kind().items():
            lines.append(f"{kind:16s} {passed:4d}/{total:<3d} {'✅' if passed == total else '❌'}")
        lines.append(f"{'TOTAL':16s} {self.passed:4d}/{self.total:<3d}   {self.score:.0%}")
        lines.append(f"{'p50 / p95':16s} {self.latency(0.5):.1f}s / {self.latency(0.95):.1f}s")
        lines.append(f"{'tokens':16s} {self.tokens:,}")
        lines.append(f"{'wall clock':16s} {self.wall_seconds:.0f}s")
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "suite": self.suite, "label": self.label, "passed": self.passed, "total": self.total,
            "wall_seconds": round(self.wall_seconds, 1), "results": [asdict(r) for r in self.results],
        }, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "Report":
        raw = json.loads(Path(path).read_text())
        return cls([Result(**r) for r in raw["results"]], raw["wall_seconds"], raw["suite"], raw["label"])


def rescore(report: Report, cases: list[dict]) -> Report:
    """The same replies, scored again — after an assertion changed, without asking the model
    again. The replies are the evidence; the scoring is an opinion about them."""
    by_id = {c["id"]: c for c in cases}
    results = []
    for r in report.results:
        if not r.answered:
            results.append(r)
            continue
        passed, reason = score(by_id[r.id], r.reply, r.tools)
        results.append(Result(r.id, r.kind, passed, reason, r.seconds, r.tokens, r.reply, r.tools, r.status))
    return Report(results, report.wall_seconds, report.suite, report.label)


async def run_suite(client: httpx.AsyncClient, cases: list[dict], concurrency: int = 4,
                    secret: str | None = None, suite: str = "", label: str = "") -> Report:
    """Run every case, `concurrency` at a time, each in a new conversation.

    New conversations are the server's to mint, which is also what fixed the first version
    of this harness: it chose its own stable thread ids, so a second run asked a service
    that remembered being asked, and got "I've already shared the details" back.
    """
    from porter.auth import issue_token

    gate = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    attempts = [case for case in cases for _ in range(max(1, int(case.get("repeats", 1))))]

    async def one(case: dict) -> Result:
        headers = {"Authorization": f"Bearer {issue_token(customer_id=case['customer_id'], secret=secret)}"}
        async with gate:
            t0 = time.perf_counter()
            try:
                response = await client.post("/v1/chat", json={"question": case["question"]},
                                             headers=headers, timeout=300.0)
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - a failed call is a failed case
                return Result(case["id"], case["kind"], False, type(exc).__name__,
                              seconds=time.perf_counter() - t0, status=0)
        if response.status_code != 200:
            return Result(case["id"], case["kind"], False, f"http {response.status_code}",
                          seconds=time.perf_counter() - t0, status=response.status_code)
        reply, tools = body.get("reply", ""), body.get("tools_used", []) or []
        usage = body.get("usage", {}) or {}
        passed, reason = score(case, reply, tools)
        return Result(case["id"], case["kind"], passed, reason,
                      seconds=usage.get("seconds", time.perf_counter() - t0),
                      tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                      reply=reply, tools=tools)

    results = await asyncio.gather(*(one(c) for c in attempts))
    return Report(list(results), time.perf_counter() - started, suite, label)


async def against_app(app, cases: list[dict], concurrency: int = 4, suite: str = "", label: str = "") -> Report:
    """The suite against an app that is already started — a notebook's, or a test's. No second
    startup: starting the app again would open a second conversation store and close it on the
    way out, from under the first."""
    from porter.settings import get_settings

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://porter") as client:
        return await run_suite(client, cases, concurrency, get_settings().auth_secret, suite, label)


async def in_process(cases: list[dict], concurrency: int = 4, suite: str = "", label: str = "") -> Report:
    """The same suite against the app object, over HTTP, with no server and no port — the
    contract, the middleware and the screens are all in the path. This is how the release
    gate runs inside the candidate image."""
    from fastapi.testclient import TestClient

    from porter.api import app
    from porter.settings import get_settings

    with TestClient(app):                            # runs the app's startup
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://porter") as client:
            return await run_suite(client, cases, concurrency, get_settings().auth_secret, suite, label)


async def against_url(url: str, cases: list[dict], concurrency: int = 4, suite: str = "",
                      label: str = "") -> Report:
    async with httpx.AsyncClient(base_url=url) as client:
        return await run_suite(client, cases, concurrency, os.environ.get("PORTER_AUTH_SECRET"), suite, label)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an evaluation suite against Porter.")
    ap.add_argument("--suite", default="smoke", help="smoke | golden | heldout | path to a .jsonl")
    ap.add_argument("--url", default=os.environ.get("PORTER_URL", ""), help="a running service")
    ap.add_argument("--in-process", action="store_true", help="against the app object, no server")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--min-score", type=float, default=float(os.environ.get("PORTER_GATE_SCORE", "0.9")))
    ap.add_argument("--save", default="", help="write the report as JSON here")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    cases = load_cases(args.suite)
    if args.in_process or not args.url:
        report = asyncio.run(in_process(cases, args.concurrency, args.suite, args.label))
    else:
        report = asyncio.run(against_url(args.url, cases, args.concurrency, args.suite, args.label))

    print(report.table())
    for f in report.failures():
        print(f"  ❌ {f.id:7s} {f.kind:15s} {f.reason}")
    if args.save:
        report.save(args.save)

    behaviour = report.behaviour_failures()
    if behaviour:
        print(f"\nFAIL — behaviour cases must all pass, and these did not: {', '.join(behaviour)}")
        return 1
    if report.score < args.min_score:
        print(f"\nFAIL — {report.score:.0%} is below the {args.min_score:.0%} this service ships at.")
        return 1
    print(f"\nPASS — {report.score:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
