"""Porter, behind HTTP.

Start it in a terminal:

    uvicorn porter.api:app --port 8080

Then http://127.0.0.1:8080/ for the desk, and /docs for the contract.

This module is the only one that knows there is a web. It does four things before the
agent runs, and each one closes a hole the notebook version had:

    who          a signed token decides the customer; a field in the body does not
    which        a conversation id only ever resolves inside the caller's own namespace
    how long     one deadline for the whole run, enforced by cancelling it — not by
                 walking away from a thread that keeps going
    what next    a return the agent opens is a request; paying it is a staff route
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from porter import agent as porter_agent
from porter import health, ledger, obs
from porter import ops as porter_ops
from porter.auth import authenticate, identify, issue_token, require_staff
from porter.schemas import (Answer, ChatRequest, ErrorBody, Health, Me, Order, Refund, ReturnRequest,
                            Version)
from porter.settings import get_ops, get_settings
from porter.tools import customer_orders

log = logging.getLogger("porter")

WEB = Path(__file__).resolve().parent / "web"
STARTED = datetime.now(timezone.utc).isoformat(timespec="seconds")
bearer = HTTPBearer(auto_error=False)      # shows an "Authorize" button at /docs


class ApiError(Exception):
    """A failure with a code a client can branch on."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


# ---------------------------------------------------------------------------------------
# lifespan — things that are expensive to open, opened once
# ---------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings, ops = get_settings(), get_ops()
    obs.setup_logging(ops.json_logs, settings.log_level)
    obs.setup_tracing(settings.phoenix_endpoint, settings.service_name)
    obs.BUILD_INFO.labels(version=settings.version, prompt_version=ops.prompt_version,
                          model=settings.model).set(1)
    # The ledger is opened once here so that a read-only or missing volume fails the start,
    # not the first customer who asks for a return.
    ledger.open_ledger(settings.ledger_path).close()
    # One checkpointer per process, not per request. Opening it per call is a measurable
    # slice of p95 on SQLite, and on Postgres it is how you run out of connections.
    async with porter_agent.open_async_checkpointer(settings) as saver:
        app.state.settings = settings
        app.state.checkpointer = saver
        log.info("porter %s up · model=%s · auth=%s", settings.version, settings.model,
                 "tokens" if settings.auth_required else "trusted caller")
        yield


app = FastAPI(title="Porter — Wickmere & Rook order desk", version=get_settings().version,
              lifespan=lifespan)


# ---------------------------------------------------------------------------------------
# every request gets an id and a version, and every failure gets the same shape
# ---------------------------------------------------------------------------------------


class RequestContext:
    """Plain ASGI middleware: one request id on the request, the log line and every error
    body, plus the version that answered — which is the first thing anybody asks after a
    deploy or a rollback. It never touches the body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = porter_ops.ensure_request_id(scope)
        t0, status = time.perf_counter(), 0

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("x-request-id", request_id)
                headers.append("x-porter-version", porter_ops.settings_for(scope).version)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info("%s %s %s %.0fms id=%s", scope.get("method"), scope.get("path"), status,
                     (time.perf_counter() - t0) * 1000, request_id)


app.add_middleware(RequestContext)
app.add_middleware(porter_ops.Gate)
app.add_middleware(porter_ops.Meter)

if get_ops().cors_origins:
    # CORS is a rule the *browser* enforces: it keeps other websites' scripts out, and does
    # nothing at all about `curl`. It is not access control.
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware,
                       allow_origins=[o.strip() for o in get_ops().cors_origins.split(",") if o.strip()],
                       allow_methods=["GET", "POST"],
                       allow_headers=["content-type", "authorization", "idempotency-key"])


def _error(request: Request, status: int, code: str, detail: str, headers: dict | None = None):
    body = ErrorBody(error=code, detail=detail, request_id=getattr(request.state, "request_id", "-"))
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", [])[1:]) or "body"
    return _error(request, 422, "invalid_request", f"{where}: {first.get('msg', 'is not valid')}")


@app.exception_handler(ApiError)
async def on_api_error(request: Request, exc: ApiError):
    return _error(request, exc.status, exc.code, exc.detail)


@app.exception_handler(HTTPException)
async def on_http_error(request: Request, exc: HTTPException):
    code = {401: "unauthorized", 403: "forbidden", 404: "thread_not_found", 409: "conflict",
            422: "invalid_request", 503: "model_unavailable", 504: "timeout"}.get(exc.status_code, "internal")
    return _error(request, exc.status_code, code, str(exc.detail), getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def on_unhandled(request: Request, exc: Exception):
    """The traceback goes to the log. It does not go to the caller, because it carries the
    database path, the SQL and the system prompt."""
    log.exception("unhandled id=%s", getattr(request.state, "request_id", "-"))
    return _error(request, 500, "internal", "Something went wrong on our side.")


# ---------------------------------------------------------------------------------------
# is it alive, and which one is it
# ---------------------------------------------------------------------------------------


@app.get("/healthz", response_model=Health)
async def healthz(request: Request) -> Health:
    """A health check that checks something — the orders database, the model endpoint, the
    ledger volume and the conversation store (porter/health.py). A handler that returns
    {"ok": true} and touches nothing reports a healthy service whose database has gone."""
    settings = request.app.state.settings
    checks = await health.check(settings)
    return Health(status="ok" if health.healthy(checks) else "degraded", version=settings.version,
                  model=settings.model, checks=checks)


@app.get("/version", response_model=Version)
async def version(request: Request) -> Version:
    settings, ops = request.app.state.settings, get_ops()
    canary = f"{ops.canary_version} at {ops.canary_percent}%" if ops.canary_version and ops.canary_percent else "none"
    return Version(version=settings.version, prompt_version=ops.prompt_version, canary=canary,
                   model=settings.model, started_at=STARTED, demo_sign_in=settings.dev_login)


# ---------------------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------------------


async def _thread(request: Request, namespace: str, thread_id: str | None) -> tuple[str, str]:
    """(public id, storage key). The key is the caller's namespace plus the id, so an id
    copied from somebody else's conversation resolves to nothing at all."""
    if thread_id:
        key = f"{namespace}:{thread_id}"
        found = await request.app.state.checkpointer.aget_tuple({"configurable": {"thread_id": key}})
        if found is None:
            raise ApiError(404, "thread_not_found", f"No conversation {thread_id!r} on this account.")
        return thread_id, key
    public = f"t-{uuid.uuid4().hex[:12]}"
    return public, f"{namespace}:{public}"


@app.post("/v1/chat", response_model=Answer)
async def chat(body: ChatRequest, request: Request,
               credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> Answer:
    """One question, one answer, and the caller waits — at most `request_timeout_s`.

    The run is awaited, not handed to a thread, so that when the time is up `wait_for`
    cancels it: the model call in flight is dropped and the graph takes no further step.
    """
    settings = request.app.state.settings
    identity = identify(request, credentials, body.customer_id)
    public, key = await _thread(request, identity.namespace, body.thread_id)
    request.state.thread_id = public
    deadline = time.monotonic() + settings.request_timeout_s

    with porter_ops.request_policy(request, body.question, public, settings) as (model, prompt):
        with obs.trace_context(public, identity.subject, request.state.request_id, prompt, model):
            try:
                result = await asyncio.wait_for(
                    porter_agent.aanswer(body.question, identity.customer_id, key, settings=settings,
                                         checkpointer=request.app.state.checkpointer,
                                         idempotency_key=idempotency_key, deadline=deadline),
                    timeout=settings.request_timeout_s,
                )
            except asyncio.TimeoutError:
                raise ApiError(504, "timeout", "The desk took too long to answer. Please try again.")
            except Exception as exc:  # noqa: BLE001
                if _model_timed_out(exc):
                    raise ApiError(504, "timeout", "The desk took too long to answer. Please try again.")
                if _looks_like_model_down(exc):
                    raise ApiError(503, "model_unavailable", "The desk is temporarily unavailable.")
                raise
    return result.model_copy(update={"thread_id": public})


@app.get("/v1/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request, customer_id: float | None = None,
                     credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """What the desk remembers about one of *your* conversations."""
    identity = identify(request, credentials, customer_id)
    key = f"{identity.namespace}:{thread_id}"
    found = await request.app.state.checkpointer.aget_tuple({"configurable": {"thread_id": key}})
    if found is None:
        raise ApiError(404, "thread_not_found", f"No conversation {thread_id!r} on this account.")
    messages = found.checkpoint["channel_values"].get("messages", [])
    return {
        "thread_id": thread_id,
        "turns": len(messages),
        "messages": [{"role": getattr(m, "type", "?"), "text": (getattr(m, "content", "") or "")[:300]}
                     for m in messages],
    }


# ---------------------------------------------------------------------------------------
# who is signed in, and their orders — what the order desk page shows beside the chat
# ---------------------------------------------------------------------------------------


@app.get("/v1/me", response_model=Me)
async def me(request: Request,
             credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Me:
    """Who this token belongs to: a customer or a member of staff. The pages ask it to decide
    what to show, and to check a token somebody pasted."""
    identity = authenticate(request, credentials)
    return Me(subject=identity.subject, role=identity.role, customer_id=identity.customer_id)


@app.get("/v1/orders", response_model=list[Order])
async def my_orders(request: Request, customer_id: float | None = None,
                    credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> list[Order]:
    """The signed-in customer's orders, newest first, each with its return request if it has
    one. Read-only, and only ever this customer's: the token decides whose."""
    identity = identify(request, credentials, customer_id)
    settings = request.app.state.settings
    orders = await run_in_threadpool(customer_orders, settings.db_path, identity.customer_id)
    returns = await run_in_threadpool(ledger.list_returns, settings.ledger_path, None, identity.customer_id)
    by_order: dict[str, list[dict]] = {}
    for row in returns:
        by_order.setdefault(row["invoice_no"], []).append(row)
    return [Order(**order, return_request=_most_relevant(by_order.get(order["invoice_no"], [])))
            for order in orders]


def _most_relevant(requests: list[dict]) -> dict | None:
    """A paid return, else the latest pending one, else the latest of any (oldest first in)."""
    for status in ("approved", "pending"):
        found = [r for r in requests if r["status"] == status]
        if found:
            return found[-1]
    return requests[-1] if requests else None


# ---------------------------------------------------------------------------------------
# refunds — staff only, and safe to press twice
# ---------------------------------------------------------------------------------------


@app.get("/v1/returns", response_model=list[ReturnRequest])
async def list_returns(request: Request, status: str | None = None,
                       credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    require_staff(request, credentials)
    return await run_in_threadpool(ledger.list_returns, request.app.state.settings.ledger_path, status)


@app.post("/v1/returns/{return_id}/approve", response_model=Refund)
async def approve_return(return_id: str, request: Request,
                         credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Refund:
    """Pay one return. Sending the same approval again returns the same refund, marked
    `replayed`, and pays nothing — so a double click and a retry after a timeout are safe."""
    staff = require_staff(request, credentials)
    try:
        refund, replayed = await run_in_threadpool(
            ledger.approve, request.app.state.settings.ledger_path, return_id, staff.subject)
    except ledger.LedgerError as exc:
        obs.RETURNS.labels(outcome=exc.code).inc()
        raise ApiError(404 if exc.code == "return_not_found" else 409, exc.code, exc.detail)
    obs.RETURNS.labels(outcome="replayed" if replayed else "paid").inc()
    return Refund(**refund, replayed=replayed)


@app.post("/v1/returns/{return_id}/reject", response_model=ReturnRequest)
async def reject_return(return_id: str, request: Request,
                        credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    staff = require_staff(request, credentials)
    try:
        row = await run_in_threadpool(ledger.reject, request.app.state.settings.ledger_path,
                                      return_id, staff.subject)
    except ledger.LedgerError as exc:
        raise ApiError(404 if exc.code == "return_not_found" else 409, exc.code, exc.detail)
    obs.RETURNS.labels(outcome="rejected").inc()
    return row


# ---------------------------------------------------------------------------------------
# the page, the numbers, and a laptop-only sign-in
# ---------------------------------------------------------------------------------------


class DevLogin(BaseModel):
    customer_id: float | None = None
    staff: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")


@app.post("/dev/token", include_in_schema=False)
async def dev_token(body: DevLogin, request: Request) -> dict:
    """Stands in for the shop's login page, and for the staff login, on a laptop. Off unless
    `PORTER_DEV_LOGIN=true`, and never on in a deployment: anybody who can reach it can be anybody."""
    if not request.app.state.settings.dev_login:
        raise HTTPException(404, "Not found.")
    if (body.customer_id is None) == (body.staff is None):
        raise HTTPException(422, "Send a customer_id or a staff name, not both.")
    return {"token": issue_token(customer_id=body.customer_id, staff=body.staff,
                                 secret=request.app.state.settings.auth_secret)}


@app.get("/", include_in_schema=False)
async def index():
    """The order desk: a customer's orders, and the chat with Porter."""
    return FileResponse(WEB / "index.html")


@app.get("/staff", include_in_schema=False)
async def staff_page():
    """The returns desk: the requests the agent opened, and the buttons that decide them."""
    return FileResponse(WEB / "staff.html")


# The two pages' shared stylesheet and script.
app.mount("/web", StaticFiles(directory=WEB), name="web")


@app.get("/metrics")
async def metrics():
    """Counters and histograms in the text format Prometheus scrapes."""
    return Response(content=obs.render_metrics(), media_type="text/plain; version=0.0.4")


def _model_timed_out(exc: Exception) -> bool:
    import openai

    return isinstance(exc, (openai.APITimeoutError, httpx.ReadTimeout))


def _looks_like_model_down(exc: Exception) -> bool:
    """Classify on the exception's type, never on its message — a database error reading
    "connection to online_retail.db failed" is not the model being down."""
    import openai

    return isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                            openai.InternalServerError, httpx.ConnectError, httpx.ReadTimeout))
