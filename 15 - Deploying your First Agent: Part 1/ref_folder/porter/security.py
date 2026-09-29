"""The door, and what is allowed through it.

Who is calling is `auth.py`'s job. This file holds the other three, and the reason they are
separate is that each fails differently:

    how much may they    a token bucket per signed-in caller, refilled by the clock
    how big may it be    a byte cap, checked before anything is parsed
    what is in it        screens, on the way in, on the way out, and on what the database
                         hands back

The last one is the only one with a hard problem in it. The first two are either right or
wrong and a test settles it. A screen is a classifier: it has a catch rate *and* a false
positive rate, and the second number is the one that decides whether it can be switched on,
because a screen that blocks four percent of genuine customers is not a safety feature, it
is an outage with a good conscience.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# =======================================================================================
# how much may they
# =======================================================================================


class TokenBucket:
    """A bucket that refills at a fixed rate and holds at most `burst`.

    A counter reset every minute is the obvious implementation and the wrong one: a caller
    who sends the whole minute's allowance in the last second and again in the first second
    of the next minute gets double the rate you thought you sold them. A bucket has no
    edges to stand on.
    """

    __slots__ = ("rate_per_second", "capacity", "_tokens", "_stamp")

    def __init__(self, per_minute: int, burst: int | None = None) -> None:
        self.rate_per_second = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self._tokens = self.capacity
        self._stamp = time.monotonic()

    def take(self, amount: float = 1.0) -> bool:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._stamp) * self.rate_per_second)
        self._stamp = now
        if self._tokens >= amount:
            self._tokens -= amount
            return True
        return False

    @property
    def retry_after(self) -> int:
        if self.rate_per_second <= 0:
            return 60
        return max(1, int((1.0 - self._tokens) / self.rate_per_second) + 1)


class Quota:
    """One bucket per caller, made on first sight."""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, identity: str, per_minute: int, burst: int) -> tuple[bool, int]:
        if per_minute <= 0:
            return True, 0
        bucket = self._buckets.get(identity)
        if bucket is None or bucket.rate_per_second != per_minute / 60.0:
            bucket = self._buckets[identity] = TokenBucket(per_minute, burst or per_minute)
        return (True, 0) if bucket.take() else (False, bucket.retry_after)

    def reset(self) -> None:
        self._buckets.clear()


QUOTA = Quota()


# =======================================================================================
# what is in it
# =======================================================================================


@dataclass
class Screen:
    """What a screen decided, and which rule decided it.

    `rule` is not decoration. Without it the only thing a blocked customer's ticket can
    say is "the robot said no", and the only way to tune a screen is to know which line
    of it is doing the damage.
    """

    verdict: str = "clean"  # clean · flag · block
    rule: str = ""
    detail: str = ""
    redacted: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.verdict != "clean"


# Rules in the order they are tried. The counts are on `traffic.jsonl`, whose ten injection
# rows are only THREE distinct sentences — the same sentences these rules were written from.
# So "10 of 10" is a score on the training set and says nothing about attempts nobody has
# seen. `evals/screen_heldout.jsonl` holds attempts and genuine messages from public sets
# that played no part in writing these rules; the notebook's appendix measures both.
INJECTION_RULES: list[tuple[str, str]] = [
    # name            catches  false positives on 790 genuine messages
    ("override", r"\b(ignore|disregard|forget)\b[^.?!]{0,40}\b(instruction|instructions|rules|prompt|above|previous|prior)\b"),      # 3   0
    ("persona", r"\b(you are now|from now on,? you|act as|pretend to be|roleplay as)\b"),                                            # 0   0
    ("exfiltrate", r"\b(system prompt|your instructions|your prompt|initial prompt|the prompt above)\b"),                            # 4   0
    ("repeat_rules", r"\b(print|repeat|reveal|show|output|display)\b[^.?!]{0,30}\b(prompt|instructions|rules|verbatim)\b"),          # 4   0
    ("jailbreak", r"\b(developer mode|jailbreak|DAN mode|without restrictions|no restrictions)\b"),                                  # 0   0
    ("tool_abuse", r"\b(call|run|execute|invoke)\b[^.?!]{0,20}\b(start_return|price_order|look_up_order|sql|query)\b"),              # 0   0
    ("scope_probe", r"\b(not just mine|someone else'?s?|other customers?|everyone else|every order|all orders|all customers)\b"),    # 3   0
]

_INJECTION = [(name, re.compile(pattern, re.I)) for name, pattern in INJECTION_RULES]

# Rules that sound right and are not. Kept as a named list rather than deleted, because the
# reason to reject one is a measurement, and a measurement you cannot re-run is an opinion.
#
#   "block anyone demanding a refund" catches **none** of the ten attempts and refuses
#   **fourteen** real customers who wanted to send something back — 1.8% of genuine traffic,
#   which on this desk is one refused customer every few minutes, permanently.
TEMPTING_AND_WRONG: list[tuple[str, str]] = [
    ("money_demand", r"\b(refund|money back)\b"),
]

PII_RULES: list[tuple[str, str]] = [
    ("email", r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
    ("card", r"\b(?:\d[ -]*?){13,19}\b"),
    ("phone", r"\b(?:\+44\s?7\d{3}|\(?07\d{3}\)?)[\s.-]?\d{3}[\s.-]?\d{3}\b"),
]

_PII = [(name, re.compile(pattern)) for name, pattern in PII_RULES]


def _luhn(digits: str) -> bool:
    """Sixteen digits in a sentence are usually not a card. Luhn is the cheap difference
    between a screen that redacts card numbers and one that redacts order histories."""
    total, alternate = 0, False
    for char in reversed(digits):
        if not char.isdigit():
            continue
        value = int(char)
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0 and len(re.sub(r"\D", "", digits)) >= 13


def screen_input(text: str, rules: list[tuple[str, re.Pattern]] | None = None) -> Screen:
    """Look at a question before the model does."""
    for name, pattern in rules if rules is not None else _INJECTION:
        match = pattern.search(text)
        if match:
            return Screen(verdict="block", rule=name, detail=match.group(0)[:80])
    return Screen()


def redact_pii(text: str) -> tuple[str, Screen]:
    """Take the personal data out before the text reaches the model, the logs, the cache
    key and the checkpoint file — which is four copies, and the reason this happens at the
    edge rather than inside the agent."""
    found: list[str] = []
    out = text
    for name, pattern in _PII:
        def replace(match: re.Match) -> str:
            value = match.group(0)
            if name == "card" and not _luhn(value):
                return value
            found.append(name)
            return f"[{name} removed]"

        out = pattern.sub(replace, out)
    return out, Screen(verdict="flag" if found else "clean", rule="pii", redacted=found)


# Phrases that only appear in the answer if the model has been talked into quoting the
# thing it was told. Fingerprints, not a classifier — a leak is a copy, so an exact match
# is the right instrument.
LEAK_FINGERPRINTS = [
    "You are Porter, the order desk",
    "How to work:",
    "look_up_order",
    "price_order",
    "start_return",
]


def screen_output(text: str) -> Screen:
    for phrase in LEAK_FINGERPRINTS:
        if phrase.lower() in text.lower():
            return Screen(verdict="block", rule="prompt_leak", detail=phrase)
    return Screen()


# Text that arrived from the database and is shaped like an instruction. The database is
# not a trusted channel: every row in it was typed by somebody, and a product description
# reaches the model in exactly the same place a customer's question does.
_TOOL_RULES = [
    ("data_injection", re.compile(r"\b(ignore|disregard)\b[^.?!]{0,40}\b(instruction|instructions|rules|prompt)\b", re.I)),
    ("data_instruction", re.compile(r"\b(system|assistant|important)\s*:\s*(you|refund|issue|approve)", re.I)),
    ("data_money", re.compile(r"\b(refund|credit|approve)\b[^.?!]{0,30}\b(immediately|in full|without)\b", re.I)),
]


def screen_tool_output(text: str) -> Screen:
    for name, pattern in _TOOL_RULES:
        match = pattern.search(text)
        if match:
            return Screen(verdict="block", rule=name, detail=match.group(0)[:80])
    return Screen()


SCRUBBED = "[removed: text in this record looked like an instruction]"


def scrub_tool_output(text: str) -> tuple[str, Screen]:
    """Take the instruction out and leave the data in.

    Refusing the whole tool result would be easier and worse: the customer asked what their
    order cost, and the answer is in the same string as the attack. A screen that turns a
    poisoned product description into a failed request has handed the attacker an outage
    instead of a refund.
    """
    verdict = screen_tool_output(text)
    if verdict.verdict != "block":
        return text, verdict
    out = text
    for _, pattern in _TOOL_RULES:
        out = pattern.sub(SCRUBBED, out)
    return out, verdict


def guard_tools(tools: list, on_block=None) -> list:
    """Put the screen between the tool and the model.

    This is the boundary that matters, and it is not the same boundary as the customer's
    message. A product description is data the shop typed years ago; by the time it reaches
    the model it is sitting in exactly the same context window as the question, in a service
    that has no way to tell them apart. Every row of every table a tool can read is an
    untrusted channel, and this is the only place to say so.
    """
    from langchain_core.tools import StructuredTool

    guarded = []
    for original in tools:
        inner = original.func

        def screened(*args, _inner=inner, _name=original.name, **kwargs):
            out = _inner(*args, **kwargs)
            clean, verdict = scrub_tool_output(str(out))
            if verdict.verdict == "block" and on_block is not None:
                on_block(_name, verdict)
            return clean

        guarded.append(
            StructuredTool.from_function(
                func=screened,
                name=original.name,
                description=original.description,
                args_schema=original.args_schema,
            )
        )
    return guarded


REFUSAL = (
    "I can't help with that one. If it is about one of your orders, send me the order "
    "number and I'll look it up."
)
