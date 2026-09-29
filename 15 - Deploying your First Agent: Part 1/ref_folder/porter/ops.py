"""The operations layer: two pieces of ASGI middleware, and the per-request policy.

This is supplied infrastructure. The contract is what matters, and it is this:

    ┌ Meter ───────────────────────────────────────────────────────────────────┐
    │  counts and times every request, including the ones refused below         │
    │ ┌ Gate ──────────────────────────────────────────────────────────────┐    │
    │ │  1  size      413  before a single byte is parsed                   │    │
    │ │  2  quota     429  a token bucket per signed-in caller              │    │
    │ │  3  screen         the question, before the model reads it          │    │
    │ │  4  cache          the same question twice costs once               │    │
    │ │  5  budget    402  what this customer has already spent today       │    │
    │ │ ┌ the route ─────────────────────────────────────────────────────┐  │    │
    │ │ │  identity → thread → policy (model, prompt version) → agent    │  │    │
    │ │ └────────────────────────────────────────────────────────────────┘  │    │
    │ │  6  screen         the answer, before the customer reads it         │    │
    │ │  7  charge         tokens against the budget; fill the cache        │    │
    │ └─────────────────────────────────────────────────────────────────────┘   │
    └───────────────────────────────────────────────────────────────────────────┘

The order is the lesson: every step is cheaper than the one below it and every step may
end the request, so the model only runs for traffic that has earned it. Every step is off
until its setting in `Ops` turns it on.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders

from porter import cost as porter_cost
from porter import security
from porter.auth import peek_identity
from porter.obs import IN_FLIGHT, LATENCY, REFUSED, REQUESTS, SCREENED, TOKENS, RunRecord
from porter.release import version_for
from porter.settings import get_ops, get_settings

CHAT_PATHS = ("/v1/chat",)
KNOWN_ROUTES = CHAT_PATHS + ("/healthz", "/version", "/metrics")

# Two decisions made per request and needed four calls deeper — which model, which prompt —
# travel in context variables rather than as arguments, so that `answer()` does not have to
# have an opinion about routing or canaries.
ROUTED_MODEL: ContextVar[str] = ContextVar("porter_routed_model", default="")
PROMPT_VERSION: ContextVar[str] = ContextVar("porter_prompt_version", default="")


def routed_model() -> str:
    return ROUTED_MODEL.get()


def active_prompt_version() -> str:
    return PROMPT_VERSION.get() or get_ops().prompt_version


def settings_for(scope: dict):
    """The settings the running app was started with — which a test or a notebook can swap
    on `app.state` — falling back to the process-wide ones."""
    state = getattr(scope.get("app"), "state", None)
    return getattr(state, "settings", None) or get_settings()


@contextmanager
def request_policy(request, question: str, public_thread_id: str, settings=None):
    """Pick the model and the prompt version for one request, and record the choice where
    the run log will find it. A canary is decided by the conversation, so it is sticky."""
    settings, ops = settings or get_settings(), get_ops()
    model, why = settings.model, "default"
    if ops.route:
        model, why = porter_cost.route(question, settings.model, ops.small_model)
    version = version_for(public_thread_id, ops.prompt_version, ops.canary_version, ops.canary_percent)

    request.state.model, request.state.route, request.state.prompt_version = model, why, version
    model_token = ROUTED_MODEL.set(model if ops.route else "")
    version_token = PROMPT_VERSION.set(version)
    try:
        yield model, version
    finally:
        ROUTED_MODEL.reset(model_token)
        PROMPT_VERSION.reset(version_token)


def ensure_request_id(scope: dict) -> str:
    """One id per request, minted by whichever layer sees the request first."""
    state = scope.setdefault("state", {})
    if not state.get("request_id"):
        headers = dict(scope.get("headers") or [])
        state["request_id"] = headers.get(b"x-request-id", b"").decode() or f"req-{uuid.uuid4().hex[:12]}"
    return state["request_id"]


# =======================================================================================
# plumbing
# =======================================================================================


def _json_response(status: int, payload: dict, request_id: str, extra_headers: dict | None = None):
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"x-request-id", request_id.encode()),
    ]
    for key, value in (extra_headers or {}).items():
        headers.append((key.encode(), str(value).encode()))

    async def send_it(send):
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return send_it


def _error(code: str, detail: str, request_id: str) -> dict:
    return {"error": code, "detail": detail, "request_id": request_id}


async def _read_body(receive):
    """Drain the request body, and hand back a `receive` that replays it. Consuming the body
    without a replay leaves the handler waiting forever — a hang, not an error."""
    chunks, more = [], True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.append(message.get("body", b""))
        more = message.get("more_body", False)
    return _replayable(b"".join(chunks))


def _replayable(body: bytes):
    served = False

    async def replay():
        nonlocal served
        if not served:
            served = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return body, replay


# =======================================================================================
# Meter — every request, counted and timed
# =======================================================================================


class Meter:
    """Outermost, so what it counts includes what the gate refused. A metric that only sees
    requests which reached a handler reports a healthy service during the incident."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not get_ops().metrics:
            return await self.app(scope, receive, send)

        path = scope.get("path", "-")
        # Label by route, never by the raw path: one label value per thread id is a
        # cardinality explosion, which is how instrumentation takes a monitoring system down.
        label = path if path in KNOWN_ROUTES else ("/v1/returns" if path.startswith("/v1/returns") else "other")
        status = 500
        start = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        IN_FLIGHT.labels(route=label).inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            IN_FLIGHT.labels(route=label).dec()
            REQUESTS.labels(route=label, status=str(status)).inc()
            LATENCY.labels(route=label).observe(time.perf_counter() - start)


# =======================================================================================
# Gate — size, quota, screens, cache, budget
# =======================================================================================


class Gate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        ops, settings = get_ops(), settings_for(scope)
        path = scope.get("path", "")
        request_id = ensure_request_id(scope)
        headers = dict(scope.get("headers") or [])

        # --- 1 · size --------------------------------------------------------------------
        if ops.max_body_bytes:
            declared = int(headers.get(b"content-length", b"0") or 0)
            if declared > ops.max_body_bytes:
                REFUSED.labels(reason="too_large").inc()
                return await _json_response(
                    413, _error("too_large", "That message is too large for this desk.", request_id),
                    request_id)(send)

        # --- 2 · quota -------------------------------------------------------------------
        # Per signed-in caller, so one busy customer cannot use up everybody's allowance;
        # per address for anybody without a valid token.
        identity = peek_identity(headers, settings)
        caller = identity.subject if identity else f"ip:{(scope.get('client') or ('-',))[0]}"
        if ops.rate_limit_per_minute and path.startswith("/v1/"):
            allowed, retry_after = security.QUOTA.allow(caller, ops.rate_limit_per_minute,
                                                         ops.rate_limit_burst)
            if not allowed:
                REFUSED.labels(reason="rate_limited").inc()
                return await _json_response(
                    429, _error("rate_limited", "Too many requests. Try again shortly.", request_id),
                    request_id, {"retry-after": retry_after})(send)

        if path not in CHAT_PATHS or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        body, replay = await _read_body(receive)
        try:
            payload = json.loads(body or b"{}")
            question = str(payload.get("question", ""))
            claimed = payload.get("customer_id")
        except Exception:  # noqa: BLE001 - a body this layer cannot read is the route's problem
            return await self.app(scope, replay, send)

        if identity and identity.customer_id is not None:
            customer = identity.customer_id
        else:
            customer = float(claimed) if isinstance(claimed, (int, float)) else 0.0

        record = RunRecord(request_id=request_id, customer=str(int(customer)),
                           version=settings.version, model=settings.model)
        started = time.perf_counter()

        # --- 3 · screen the question -------------------------------------------------------
        if ops.redact_pii:
            question, pii = security.redact_pii(question)
            if pii:
                for kind in pii.redacted:
                    SCREENED.labels(where="input", rule=f"pii:{kind}").inc()
                record.screen = "pii_redacted"
                payload["question"] = question
                body, replay = _replayable(json.dumps(payload).encode())

        if ops.screen_input:
            verdict = security.screen_input(question)
            if verdict.verdict == "block":
                SCREENED.labels(where="input", rule=verdict.rule).inc()
                record.screen = f"blocked:{verdict.rule}"
                record.seconds = round(time.perf_counter() - started, 3)
                record.emit()
                # A refusal is a 200 with an answer in it, not an error: the customer asked
                # a question and got a reply. Only the log knows a rule fired.
                return await _json_response(200, {
                    "thread_id": payload.get("thread_id") or "-", "reply": security.REFUSAL,
                    "tools_used": [], "usage": {"input_tokens": 0, "output_tokens": 0,
                                                "model_calls": 0, "tool_calls": 0, "seconds": 0.0},
                    "stopped_at_limit": False}, request_id)(send)

        # --- 4 · cache ---------------------------------------------------------------------
        cache, key = None, ""
        if ops.cache and porter_cost.cacheable(question) and not payload.get("thread_id"):
            policy = f"{settings.model}|{ops.prompt_version}|{ops.canary_version}:{ops.canary_percent}|{ops.route}"
            cache = porter_cost.ResponseCache(ops.cache_path)
            key = porter_cost.cache_key(customer, question, policy, ops.prompt_version)
            hit = cache.get(key)
            if hit is not None:
                cache.close()
                record.cache = "hit"
                record.seconds = round(time.perf_counter() - started, 3)
                record.emit()
                return await _json_response(200, hit, request_id, {"x-porter-cache": "hit"})(send)

        # --- 5 · budget --------------------------------------------------------------------
        budget = None
        if ops.daily_token_budget:
            budget = porter_cost.Budget(ops.budget_path, ops.daily_token_budget)
            if not budget.allow(customer):
                budget.close()
                if cache is not None:
                    cache.close()
                REFUSED.labels(reason="over_budget").inc()
                record.screen, record.status = "over_budget", 402
                record.emit()
                return await _json_response(
                    402, _error("over_budget", porter_cost.OVER_BUDGET, request_id), request_id)(send)

        # --- the route -----------------------------------------------------------------------
        captured: dict = {}

        async def hold(message):
            if message["type"] == "http.response.start":
                captured["start"] = message
                return                       # held until the answer has been screened
            if message["type"] == "http.response.body":
                captured.setdefault("chunks", []).append(message.get("body", b""))
                if not message.get("more_body"):
                    await self._finish(send, scope, captured, record, ops, cache, key, budget,
                                       customer, question, started)
                return
            await send(message)

        await self.app(scope, replay, hold)

    @staticmethod
    def _fill(record: RunRecord, scope: dict) -> None:
        """Copy what the route decided — the thread, the model, the prompt — onto the log line."""
        state = scope.get("state", {})
        record.thread_id = state.get("thread_id", record.thread_id)
        record.model = state.get("model", record.model)
        record.route = state.get("route", record.route)
        record.prompt_version = state.get("prompt_version", record.prompt_version)

    async def _finish(self, send, scope, captured, record, ops, cache, key, budget, customer,
                      question, started):
        raw = b"".join(captured.get("chunks", []))
        start_message = captured["start"]
        record.status = start_message["status"]
        self._fill(record, scope)

        answer = None
        if record.status == 200:
            try:
                answer = json.loads(raw)
            except Exception:  # noqa: BLE001
                answer = None

        if answer is not None:
            usage = answer.get("usage", {}) or {}
            record.input_tokens = usage.get("input_tokens", 0)
            record.output_tokens = usage.get("output_tokens", 0)
            record.model_calls = usage.get("model_calls", 0)
            record.tool_calls = usage.get("tool_calls", 0)
            record.tools = answer.get("tools_used", []) or []
            record.stopped_at_limit = answer.get("stopped_at_limit", False)
            TOKENS.labels(direction="input", model=record.model).inc(record.input_tokens)
            TOKENS.labels(direction="output", model=record.model).inc(record.output_tokens)

            # --- 6 · screen the answer --------------------------------------------------------
            if ops.screen_output:
                verdict = security.screen_output(answer.get("reply", ""))
                if verdict.verdict == "block":
                    SCREENED.labels(where="output", rule=verdict.rule).inc()
                    record.screen = f"blocked_out:{verdict.rule}"
                    answer["reply"] = security.REFUSAL
                    raw = json.dumps(answer).encode()

            # --- 7 · charge and fill ------------------------------------------------------------
            total = record.input_tokens + record.output_tokens
            if budget is not None and total:
                budget.charge(customer, total)
            if cache is not None and key and record.screen == "clean":
                cache.put(key, customer, question, answer)

        if budget is not None:
            budget.close()
        if cache is not None:
            cache.close()

        record.seconds = round(time.perf_counter() - started, 3)
        record.emit()

        out_headers = MutableHeaders(scope=start_message)
        out_headers["content-length"] = str(len(raw))
        if cache is not None:
            out_headers["x-porter-cache"] = "miss"
        await send(start_message)
        await send({"type": "http.response.body", "body": raw})
