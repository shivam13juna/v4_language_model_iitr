"""Porter over HTTP, and the numbers Prometheus reads.

    uvicorn porter.api:app --port 8080

    GET  /                                the chat page
    POST /v1/chat                         one question, one answer, in a conversation
    GET  /staff                           the returns desk
    GET  /v1/returns                      every return request
    POST /v1/returns/{return_id}/approve  a member of staff pays one, once
    GET  /healthz                         up, and able to read the orders
    GET  /metrics                         the numbers, for Prometheus
"""

import time
from pathlib import Path

import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, disable_created_metrics, generate_latest
from pydantic import BaseModel, Field

from porter.agent import answer
from porter.ledger import approve, list_returns
from porter.settings import settings
from porter.tools import read_only

app = FastAPI(title="Porter, the Wickmere & Rook order desk")

# --- the numbers -----------------------------------------------------------------------------
disable_created_metrics()          # no *_created timestamp lines: /metrics stays short
REQUESTS = Counter("porter_requests_total", "Requests answered, by route and status code.", ["route", "status"])
LATENCY = Histogram("porter_request_seconds", "Seconds to answer, by route.", ["route"],
                    buckets=[0.1, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60])   # around this service's latencies
TOKENS = Counter("porter_tokens_total", "Model tokens, input and output.", ["kind"])


@app.middleware("http")
async def measure(request: Request, call_next):
    """Around every request: count it, by route and status code, and time it."""
    start, status = time.perf_counter(), 500           # a request that crashes counts as a 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # The label is the route that matched, such as /v1/returns/{return_id}/approve, never the
        # URL itself: one label value per URL anybody types would mean thousands of series.
        route = request.scope["route"].path if "route" in request.scope else "other"
        REQUESTS.labels(route, status).inc()
        LATENCY.labels(route).observe(time.perf_counter() - start)


@app.get("/metrics")
def metrics():
    """Every number above, as text. Prometheus reads this page every 5 seconds."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- the routes ------------------------------------------------------------------------------
class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000, examples=["How much was order 580638?"])
    customer_id: int = Field(examples=[12381])
    # The conversation to carry on: the thread_id of an earlier answer. Leave it out to start one.
    thread_id: str | None = Field(None, max_length=64, examples=["demo"])


@app.post("/v1/chat")
def chat(body: Question):
    """One question, one answer: the conversation's thread_id, the reply, the tools used, the tokens spent."""
    try:
        result = answer(body.question, body.customer_id, body.thread_id)
    except openai.OpenAIError as exc:                   # no key, a wrong key, a rate limit, a timeout
        raise HTTPException(503, f"The model didn't answer: {type(exc).__name__}")
    TOKENS.labels("input").inc(result["input_tokens"])
    TOKENS.labels("output").inc(result["output_tokens"])
    return result


@app.get("/healthz")
def healthz():
    """Up, and able to read the orders. A missing database fails here, as a 500."""
    con = read_only(settings.db_path)
    con.execute("SELECT 1 FROM invoices LIMIT 1")
    con.close()
    return {"status": "ok", "model": settings.model, "api_key": "set" if settings.api_key else "missing"}


@app.get("/v1/returns")
def returns():
    """Every return request, oldest first, with its refund once a member of staff has paid it."""
    return list_returns(settings.ledger_path)


@app.post("/v1/returns/{return_id}/approve")
def approve_return(return_id: str, staff: str):
    """A member of staff pays one return request. Approving it again pays nothing: it returns the
    first refund, marked replayed. There is no sign-in, so the caller names the member of staff."""
    refund, replayed = approve(settings.ledger_path, return_id, staff)
    return {**refund, "replayed": replayed}


@app.get("/", include_in_schema=False)
def page():
    """The chat page."""
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/staff", include_in_schema=False)
def staff_page():
    """The returns desk: every request, and a button that approves it."""
    return FileResponse(Path(__file__).parent / "web" / "staff.html")
