"""Seeing inside a running service: a log line, a trace, and a metric.

Three outputs, and they answer three different questions:

    a log line   one JSON object per run — who, which version, how many tokens, how long.
                 Greppable, and the only one of the three that needs no infrastructure.
    a trace      the same run as a tree of spans — each model call, each tool call, with
                 its input and output — so "why did it do that" lands on a step. Sent to
                 Phoenix over OpenTelemetry when `PORTER_PHOENIX_ENDPOINT` is set.
    a metric     counters and histograms over every run. The only one you can alert on and
                 draw on a dashboard, because it is the only one already aggregated.
                 Scraped from `/metrics` by Prometheus; drawn by Grafana.

The tracing is not hand-written. OpenInference's LangChain instrumentor hooks the callback
system every LangChain and LangGraph run already goes through, so `answer()` does not know
it is being traced — which is the property that lets you add tracing to a service without
touching the code you are trying to observe.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

log = logging.getLogger("porter")
run_log = logging.getLogger("porter.run")


# =======================================================================================
# 1 · one JSON object per run
# =======================================================================================


class JsonFormatter(logging.Formatter):
    """Every line a machine can parse, with the request id on all of them."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in getattr(record, "fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(json_logs: bool, level: str = "info") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if json_logs
        else logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


@dataclass
class RunRecord:
    """The one line per run that a support ticket is answered from.

    Not on it: the question and the reply. Both are customer text, and a log that carries
    customer text is a log with a retention policy and an access-control problem attached.
    The thread id is enough to find the conversation for somebody entitled to read it.
    """

    request_id: str = "-"
    thread_id: str = "-"
    customer: str = "-"
    version: str = "-"
    prompt_version: str = "-"
    route: str = "-"
    model: str = "-"
    tools: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    seconds: float = 0.0
    cache: str = "miss"
    screen: str = "clean"
    stopped_at_limit: bool = False
    status: int = 200

    def emit(self) -> None:
        run_log.info("run", extra={"fields": asdict(self)})


# =======================================================================================
# 2 · traces, to Phoenix
# =======================================================================================

_tracing_on = False


def setup_tracing(endpoint: str, project: str = "porter") -> bool:
    """Send every LangChain/LangGraph run to Phoenix as OpenTelemetry spans. Once per process."""
    global _tracing_on
    if _tracing_on or not endpoint:
        return _tracing_on
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    provider = register(endpoint=endpoint, project_name=project, batch=True, verbose=False,
                        set_global_tracer_provider=False)
    LangChainInstrumentor().instrument(tracer_provider=provider)
    _tracing_on = True
    return True


def trace_context(thread_id: str, customer: str, request_id: str, prompt_version: str, model: str):
    """Attributes stamped on every span of one run, so a trace can be found by conversation,
    by customer, or by the request id in a log line. A no-op when tracing is off."""
    if not _tracing_on:
        return contextlib.nullcontext()
    from openinference.instrumentation import using_attributes

    return using_attributes(
        session_id=thread_id,
        user_id=customer,
        metadata={"request_id": request_id, "prompt_version": prompt_version, "model": model},
    )


# =======================================================================================
# 3 · numbers you can alert on
# =======================================================================================

REGISTRY = CollectorRegistry()

REQUESTS = Counter(
    "porter_requests_total", "Requests by route and status.", ["route", "status"], registry=REGISTRY
)
LATENCY = Histogram(
    "porter_request_seconds",
    "End to end request latency.",
    ["route"],
    # Default buckets top out at 10s, which puts every interesting request in +Inf on a
    # service whose p99 is a model call. Pick buckets around the latency you actually have.
    buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60, 120),
    registry=REGISTRY,
)
IN_FLIGHT = Gauge(
    "porter_in_flight", "Requests being worked on right now.", ["route"], registry=REGISTRY
)
TOKENS = Counter(
    "porter_tokens_total", "Tokens by direction and model.", ["direction", "model"], registry=REGISTRY
)
SCREENED = Counter(
    "porter_screened_total", "Requests a screen acted on.", ["where", "rule"], registry=REGISTRY
)
REFUSED = Counter(
    "porter_refused_total", "Requests refused before the model.", ["reason"], registry=REGISTRY
)
RETURNS = Counter(
    "porter_returns_total", "Return requests and refunds, by outcome.", ["outcome"], registry=REGISTRY
)
# Always 1. The labels are the point: a dashboard panel and an alert can both say which
# version is serving, which is the first question after every deploy and every rollback.
BUILD_INFO = Gauge(
    "porter_build_info", "The version this process is running.",
    ["version", "prompt_version", "model"], registry=REGISTRY,
)


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
