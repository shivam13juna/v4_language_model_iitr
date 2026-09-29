"""Every knob Porter has, in one place, read from the environment.

A notebook reads its configuration from whatever happens to be in the kernel. A service
reads it from the environment, once, at startup, and refuses to start if the values do not
make sense together. That is the whole difference, and it is why this file exists before
any of the others.

Every field can be overridden with a `PORTER_`-prefixed environment variable, or a line in
`.env` (see `.env.example`):

    PORTER_PROVIDER=ollama PORTER_LOG_LEVEL=debug uvicorn porter.api:app --port 8080

Two settings objects, because the fields change on two different clocks:

    Settings   what the service IS — the model, the data, the limits, who may call it.
               Changing one of these is a code decision and goes through a release.
    Ops        how it is OPERATED — rate limits, screens, the canary, the output ceiling.
               These get changed to end an incident, by whoever is awake.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The folder that holds this package, so a relative default still works when the process
# is started from somewhere else — which a container always does.
ROOT = Path(__file__).resolve().parent.parent

DEV_SECRET = "dev-only-not-a-secret-set-PORTER_AUTH_SECRET"

# The two places the model can run. Both serve OpenAI's Chat Completions API, so the client,
# the tools and the agent are the same code for either; what differs is which model, at what
# address, and whether a key is checked.
PROVIDERS = {
    "openai": {"model": "gpt-4.1-mini", "base_url": "https://api.openai.com/v1"},
    "ollama": {"model": "gpt-oss:20b", "base_url": "http://127.0.0.1:11434/v1"},
}


def _absolute(v: Path) -> Path:
    """A relative path means 'wherever this process happens to be', which is a bug waiting
    for a container. Resolve against the repo root instead."""
    v = Path(v)
    return v if v.is_absolute() else (ROOT / v).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTER_", env_file=(ROOT / ".env"), extra="ignore")

    # --- the model -------------------------------------------------------------------
    # PORTER_PROVIDER picks where the model runs: "openai" (the default) or "ollama". The
    # provider fills in the three fields after it, unless they are set: PORTER_MODEL for a
    # different model; PORTER_BASE_URL and PORTER_API_KEY for Ollama through the tunnel,
    # where the key is the tunnel's token.
    provider: Literal["openai", "ollama"] = "openai"
    model: str = ""                       # openai: gpt-4.1-mini · ollama: gpt-oss:20b
    base_url: str = ""                    # openai: api.openai.com · ollama: ollama_url
    api_key: str = ""                     # openai: OPENAI_API_KEY · Ollama checks none
    # Read from OPENAI_API_KEY, without the prefix: the name OpenAI's own tools use.
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")
    # Where Ollama answers. Inside a container that is the host, so compose sets it.
    ollama_url: str = PROVIDERS["ollama"]["base_url"]
    temperature: float = 0.0              # gpt-4.1 honours 0; gpt-5 and o-series refuse it
    model_timeout_s: float = 120.0        # one model call — also the cap on a runaway one
    model_max_retries: int = 2            # dropped connections, 5xx and 429 only, never timeouts

    # --- data ------------------------------------------------------------------------
    db_path: Path = ROOT / "online_retail.db"                 # the shop's orders, read-only
    checkpoint_path: Path = ROOT / "porter_threads.sqlite"    # conversations
    ledger_path: Path = ROOT / "porter_ledger.sqlite"         # return requests and refunds
    postgres_dsn: str = ""                # set it and conversations move off SQLite

    # --- what one request is allowed to do -------------------------------------------
    max_model_calls: int = 6
    max_tool_calls: int = 8
    request_timeout_s: float = 150.0      # the caller's budget for the whole run

    # --- who may call it ---------------------------------------------------------------
    auth_required: bool = True            # False = trust the customer_id in the body
    auth_secret: str = DEV_SECRET         # signs and checks bearer tokens
    token_ttl_s: int = 8 * 3600
    dev_login: bool = False               # POST /dev/token, for the web page on a laptop

    # --- the service ------------------------------------------------------------------
    service_name: str = "porter"
    version: str = "dev"                  # baked in at build time: --build-arg PORTER_VERSION
    log_level: str = "info"
    phoenix_endpoint: str = ""            # e.g. http://localhost:6006/v1/traces — empty is off

    def model_post_init(self, _context) -> None:
        self.db_path = _absolute(self.db_path)
        self.checkpoint_path = _absolute(self.checkpoint_path)
        self.ledger_path = _absolute(self.ledger_path)

        # The provider fills in whatever the environment left empty.
        self.model = self.model or PROVIDERS[self.provider]["model"]
        self.base_url = self.base_url or (self.ollama_url if self.provider == "ollama"
                                          else PROVIDERS["openai"]["base_url"])
        # The OpenAI key goes to OpenAI's address and nowhere else. Any other endpoint gets
        # PORTER_API_KEY if it is set (the tunnel's token), or a placeholder: Ollama checks none.
        if not self.api_key:
            self.api_key = (self.openai_api_key if self.base_url.startswith(PROVIDERS["openai"]["base_url"])
                            else "ollama")

        # A downstream call that is allowed to outlive the request it serves is a call that
        # keeps running after the caller has been told it failed. The budget is checked
        # here, at startup, rather than discovered in an incident. Timeouts are never
        # retried (agent.py), so one model call costs at most model_timeout_s.
        if self.model_timeout_s >= self.request_timeout_s:
            raise ValueError(
                f"model_timeout_s = {self.model_timeout_s:.0f}s does not fit "
                f"inside request_timeout_s = {self.request_timeout_s:.0f}s"
            )

    @property
    def is_scripted(self) -> bool:
        """A stand-in model that follows a script — for tests and failure drills."""
        return self.model.startswith("scripted:")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """One Settings object per process. Cached, so importing it from five modules does not
    read the environment five times — and so a test can clear the cache and swap it."""
    return Settings()


class Ops(BaseSettings):
    """How the service is *operated*, as opposed to what it is.

    Every one of these is **off by default**, and off means the service behaves exactly as
    it would without the control. That is the only way to say what a control is worth: the
    measurement either side of the switch is the argument for it.

        PORTER_RATE_LIMIT_PER_MINUTE=30 PORTER_SCREEN_TOOL_OUTPUT=true uvicorn porter.api:app
    """

    model_config = SettingsConfigDict(env_prefix="PORTER_", env_file=(ROOT / ".env"), extra="ignore")

    # --- the door ---------------------------------------------------------------------
    rate_limit_per_minute: int = 0        # per caller. 0 is off.
    rate_limit_burst: int = 0             # how much of a minute may arrive at once
    max_body_bytes: int = 0               # 0 is off
    cors_origins: str = ""                # comma separated; empty sends no CORS headers

    # --- what comes through it ---------------------------------------------------------
    screen_input: bool = False            # look at the question before the model does
    screen_output: bool = False           # look at the answer before the customer does
    screen_tool_output: bool = False      # look at what the database hands back
    redact_pii: bool = False

    # --- what it costs ------------------------------------------------------------------
    cache: bool = False
    cache_path: Path = ROOT / "porter_cache.sqlite"
    daily_token_budget: int = 0           # per customer. 0 is off.
    budget_path: Path = ROOT / "porter_budget.sqlite"
    route: bool = False                   # send read-only questions to a smaller model
    small_model: str = "llama3.1:8b"
    max_output_tokens: int = 0            # 0 leaves it to the model

    # --- shipping a change ----------------------------------------------------------------
    prompt_version: str = "v1"
    canary_version: str = ""              # the version the canary arm runs
    canary_percent: int = 0               # 0–100, by hash of thread_id

    # --- seeing inside it -----------------------------------------------------------------
    json_logs: bool = False
    metrics: bool = False

    def model_post_init(self, _context) -> None:
        self.cache_path = _absolute(self.cache_path)
        self.budget_path = _absolute(self.budget_path)


@lru_cache(maxsize=1)
def _load_ops() -> Ops:
    return Ops()


_override: Ops | None = None


def get_ops() -> Ops:
    """The operations policy in force right now."""
    return _override if _override is not None else _load_ops()


@contextmanager
def ops_override(**fields):
    """Run a block under a different policy — one process, both sides of a switch."""
    global _override
    previous = _override
    _override = (previous if previous is not None else _load_ops()).model_copy(update=fields)
    try:
        yield _override
    finally:
        _override = previous
