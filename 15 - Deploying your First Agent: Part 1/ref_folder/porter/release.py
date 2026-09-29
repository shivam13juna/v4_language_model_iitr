"""Shipping a change to a service that is already answering people.

Two things live here, and they are the two halves of the same problem.

**Prompts as versioned config.** `porter/prompts/v1.md` and `v2.md` are files, loaded by
name. The reason to pull them out of `agent.py` is not tidiness — it is that a prompt is
the part of an agent most likely to change and least likely to be reviewed, and a version
number is what makes "when did it start doing that" answerable.

The nuance worth stating out loud: moving prompts into files does **not** make them
config. They still ship inside the image, so changing one is still a release and still
goes through the gate. Moving them into a database *would* make them config, and that is
the point at which a prompt change quietly skips every test you wrote — which is a real
design, used deliberately by teams who want to tune copy without a deploy, and a trap for
anyone who arrives at it by accident.

**Which version a request gets.** A canary is a percentage of live traffic on the new one,
decided by a hash of the conversation rather than by a coin toss, because a customer who
gets v1 for one turn and v2 for the next is being served by two different agents in the
same conversation.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=8)
def load_prompt(version: str) -> str:
    """Read one prompt version. Cached — this is on the path of every request."""
    path = PROMPT_DIR / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No prompt {version!r}. Have: {sorted(p.stem for p in PROMPT_DIR.glob('*.md'))}"
        )
    return path.read_text().strip()


def versions() -> list[str]:
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))


def arm_for(thread_id: str, percent: int) -> str:
    """Which arm a conversation belongs to: `stable` or `canary`.

    A hash, not `random()`. Three properties follow from that and all three are load
    bearing:

      * **sticky** — the same conversation always lands in the same arm, so a customer
        never sees two versions in one exchange;
      * **stateless** — every process computes the same answer with no shared store, which
        is what lets you run four replicas behind a proxy;
      * **repeatable** — after an incident you can work out which arm a given thread was
        in, months later, from the thread id alone.

    The split is only approximately `percent`; over eight hundred conversations it lands
    within a point or two, and a canary that has to be exactly ten percent is a canary
    measuring the wrong thing.
    """
    if percent <= 0:
        return "stable"
    if percent >= 100:
        return "canary"
    digest = hashlib.sha256(thread_id.encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return "canary" if bucket < percent else "stable"


def version_for(thread_id: str | None, prompt_version: str, canary_version: str, canary_percent: int) -> str:
    """The prompt version this conversation runs on."""
    if not canary_version or canary_percent <= 0 or not thread_id:
        return prompt_version
    return canary_version if arm_for(thread_id, canary_percent) == "canary" else prompt_version
