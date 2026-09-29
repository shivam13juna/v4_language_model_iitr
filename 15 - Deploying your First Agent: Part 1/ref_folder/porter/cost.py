"""What a request costs, and the four ways to stop it costing more.

Running the model yourself does not make a request free. It changes the currency. A hosted
model bills tokens; a model on your own machine bills **seconds of a box you are paying for
either way**, and the bill arrives as capacity rather than as an invoice — which is worse,
because you notice an invoice.

So there are two prices here and both are real:

    pence(usage, card)      what those exact tokens would have cost on someone's rate card
    machine_pence(seconds)  what the wall-clock cost on a box you rent by the hour

and four controls, in the order they are worth doing:

    1. a ceiling        the run stops rather than growing without limit    (Workshop 1 P5)
    2. a cache          the same question twice costs once
    3. routing          the cheap model handles what it can be trusted with
    4. a budget         one customer cannot spend the month in an afternoon

The order matters. A cache is worth more than routing on a desk where the same forty
questions arrive all day, and routing is worth nothing at all if the small model does
something expensive — which is the measurement in Workshop 2 P4, not a slogan.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

# =======================================================================================
# prices
# =======================================================================================

# Pence per million tokens. These are inputs to the arithmetic, not a price list: check
# the vendor's own pricing page before you quote any of them anywhere. The method is what
# transfers; the numbers change every few months.
RATE_CARDS: dict[str, dict[str, float]] = {
    "hosted-small": {"input": 12.0, "output": 48.0},
    "hosted-large": {"input": 200.0, "output": 800.0},
    "local": {"input": 0.0, "output": 0.0},
}

# What the machine costs per hour, however you happen to pay for it — a cloud instance, a
# share of a workstation, or the laptop's depreciation and the electricity. Any figure you
# can defend is better than pretending the number is zero.
MACHINE_PENCE_PER_HOUR = 40.0


def pence(input_tokens: int, output_tokens: int, card: str = "hosted-small") -> float:
    rates = RATE_CARDS[card]
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def machine_pence(seconds: float) -> float:
    return seconds * MACHINE_PENCE_PER_HOUR / 3600.0


# =======================================================================================
# 2 · the same question twice costs once
# =======================================================================================

_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def cache_key(customer_id: float, question: str, model: str, prompt_version: str) -> str:
    """Everything that could change the answer goes in the key.

    `customer_id` is in it because the same sentence means a different thing to two
    different people, and a cache that forgets this is the fastest way to hand one
    customer another customer's order. `model` and `prompt_version` are in it because a
    release that changes either one must not be served yesterday's answers.
    """
    normalised = _SPACES.sub(" ", _PUNCTUATION.sub(" ", question.lower())).strip()
    raw = f"{customer_id}|{normalised}|{model}|{prompt_version}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class ResponseCache:
    """Exact match, on disk, with a time to live.

    Exact match is the honest starting point and the notebook measures what it is worth on
    real traffic before suggesting anything cleverer. Semantic caching — embed the
    question, serve the nearest neighbour — raises the hit rate and introduces a failure
    mode this one cannot have: serving a confidently wrong answer to a question nobody
    asked.
    """

    def __init__(self, path: Path, ttl_seconds: int = 3600) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                customer_id REAL NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                hits        INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.con.commit()

    def get(self, key: str) -> dict | None:
        row = self.con.execute("SELECT answer, created_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds()
        if age > self.ttl_seconds:
            self.con.execute("DELETE FROM cache WHERE key = ?", (key,))
            self.con.commit()
            return None
        self.con.execute("UPDATE cache SET hits = hits + 1 WHERE key = ?", (key,))
        self.con.commit()
        return json.loads(row[0])

    def put(self, key: str, customer_id: float, question: str, answer: dict) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?, 0)",
            (
                key,
                customer_id,
                question[:500],
                json.dumps(answer),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self.con.commit()

    def stats(self) -> dict:
        rows, hits = self.con.execute("SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM cache").fetchone()
        return {"entries": rows, "hits": hits}

    def close(self) -> None:
        self.con.close()


# A question that spends money is never served from a cache. "I want to return 580638"
# answered from yesterday's copy is a refund that did not happen and a customer who thinks
# it did.
NEVER_CACHE = re.compile(
    r"\b(return|refund|send\s+(it|them|this|that)?\s*back|cancel|money\s+back|replace|exchange)\b",
    re.I,
)


def cacheable(question: str) -> bool:
    return NEVER_CACHE.search(question) is None


# =======================================================================================
# 3 · the cheap model handles what it can be trusted with
# =======================================================================================

# Route by blast radius, not by difficulty.
#
# The obvious rule is "easy questions to the small model". Measured on this machine it is
# also the wrong one: the small model answers easy questions *faster* than the large one
# and just as correctly, and then, given a vague sentence about returning something one
# day, invents an order number and calls the refund tool with it. Difficulty is not what
# separates the two models. What a mistake costs is.
SPENDS_MONEY = re.compile(
    r"\b(return|refund|send\s+(it|them|this|that)?\s*back|cancel|money\s+back|replace|"
    r"exchange|damaged|broken|faulty|wrong\s+item|missing)\b",
    re.I,
)


def route(question: str, large: str, small: str) -> tuple[str, str]:
    """Pick a model for this question, and say why.

    The reason is returned with the choice because it ends up in the run log, and six weeks
    later "why did this one go to the small model" is a question somebody will ask about a
    specific request id.
    """
    if SPENDS_MONEY.search(question):
        return large, "may spend money"
    if len(question) > 300:
        return large, "long"
    return small, "read only"


# =======================================================================================
# 4 · one customer cannot spend the month in an afternoon
# =======================================================================================


class Budget:
    """Tokens per customer per day, counted in a file rather than in memory.

    In memory it resets on deploy, and it is wrong the moment there are two processes —
    which there are, because Workshop 1 finished with a container and containers come in
    numbers. The row is the shared fact; the process is not.
    """

    def __init__(self, path: Path, daily_tokens: int) -> None:
        self.path = Path(path)
        self.daily_tokens = daily_tokens
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS spend (
                customer_id REAL NOT NULL,
                day         TEXT NOT NULL,
                tokens      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (customer_id, day)
            )
            """
        )
        self.con.commit()

    def spent_today(self, customer_id: float) -> int:
        row = self.con.execute(
            "SELECT tokens FROM spend WHERE customer_id = ? AND day = ?",
            (customer_id, date.today().isoformat()),
        ).fetchone()
        return row[0] if row else 0

    def allow(self, customer_id: float) -> bool:
        if self.daily_tokens <= 0:
            return True
        return self.spent_today(customer_id) < self.daily_tokens

    def charge(self, customer_id: float, tokens: int) -> int:
        """Charged *after* the run, because nobody knows what a question costs until it has
        been answered. The consequence is that the budget is always one request behind —
        a customer's last request of the day may overshoot, and the hard stop catches the
        one after it. A per-request ceiling is what stops that overshoot being unbounded,
        which is why the two controls belong together."""
        self.con.execute(
            """
            INSERT INTO spend (customer_id, day, tokens) VALUES (?, ?, ?)
            ON CONFLICT(customer_id, day) DO UPDATE SET tokens = tokens + excluded.tokens
            """,
            (customer_id, date.today().isoformat(), tokens),
        )
        self.con.commit()
        return self.spent_today(customer_id)

    def close(self) -> None:
        self.con.close()


OVER_BUDGET = (
    "You have reached today's limit on this desk. It resets tomorrow, or you can reply to "
    "your order confirmation email and a person will pick it up."
)
