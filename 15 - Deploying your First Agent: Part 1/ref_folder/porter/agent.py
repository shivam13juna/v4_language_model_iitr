"""Porter itself: one function in, one typed answer out.

Everything a notebook agent keeps in the kernel is a parameter here. Nothing in this file
reads a global, prints, or assumes a working directory. That is what makes it importable
from a web process, a test, a worker and a load generator without changing a line.

    from porter.agent import answer
    answer("How much was order 580638?", customer_id=12381.0)

Two entry points, and the web process uses the second:

    answer()     blocking — for scripts, tests and a subprocess
    aanswer()    async — so that when the caller's time runs out, cancelling the task
                 actually stops the run. A blocking call handed to a thread cannot be
                 stopped that way: the caller gets its timeout and the thread carries on,
                 tools and all.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from porter.release import load_prompt
from porter.schemas import Answer, Usage
from porter.settings import Settings, get_ops, get_settings
from porter.tools import build_tools

# The text lives in `porter/prompts/`, one file per version — see release.py.
SYSTEM_PROMPT = load_prompt("v1")

# What the customer reads when a limit ended the run. The limit's own text is for developers;
# `stopped_at_limit` on the answer tells a caller it happened.
LIMIT_REPLY = ("That needs more steps than I can take in one go. Could you ask about one order "
               "at a time?")


def _system_prompt() -> str:
    """The prompt this request is on: a canary, a rollback, or whatever the settings say."""
    from porter.ops import active_prompt_version

    return load_prompt(active_prompt_version())


def _model_name(settings: Settings) -> str:
    """Whatever the settings say, unless this request was routed somewhere else."""
    from porter.ops import routed_model

    return routed_model() or settings.model


# ---------------------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------------------


def build_model(settings: Settings):
    """The model, wherever the settings put it. OpenAI and Ollama both serve the Chat
    Completions API, so this is the same client for either: PORTER_PROVIDER picks the model,
    the address and the key (settings.py)."""
    name = _model_name(settings)
    if name.startswith("scripted:"):
        from porter import scripted

        return scripted.build(name)

    ceiling = get_ops().max_output_tokens
    return ChatOpenAI(
        model=name,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=settings.temperature,
        # One model call's budget. It must fit inside the request's — settings.py refuses to
        # start if it does not. No retries here: the client would retry timeouts too, and
        # retrying a call that timed out on a saturated model re-queues the same work.
        # Retries are the middleware's job, below, and it chooses what to retry.
        timeout=settings.model_timeout_s,
        max_retries=0,
        # `extra_body`, not `max_tokens=`: ChatOpenAI translates `max_tokens` into
        # `max_completion_tokens`, which Ollama ignores. Measured: 8,148 tokens without the
        # ceiling and 8,162 with it. `extra_body` puts the field on the wire untranslated.
        extra_body={"max_tokens": ceiling} if ceiling else None,
    )


def _transient(exc: Exception) -> bool:
    """Worth another attempt: the connection dropped, the server erred, or it asked us to
    slow down. Not worth one: a timeout (APITimeoutError is a kind of APIConnectionError)."""
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return False
    return isinstance(exc, (openai.APIConnectionError, openai.InternalServerError, openai.RateLimitError))


def _tools(customer_id: float, settings: Settings, idempotency_key: str | None,
           deadline: float | None):
    from porter.security import guard_tools

    tools = build_tools(customer_id, settings.db_path, settings.ledger_path,
                        idempotency_key=idempotency_key, deadline=deadline)
    return guard_tools(tools) if get_ops().screen_tool_output else tools


def build_agent(customer_id: float, settings: Settings, checkpointer,
                idempotency_key: str | None = None, deadline: float | None = None):
    """A compiled agent scoped to one customer and one request.

    Built per request, not once at startup, because the tools close over the customer, the
    idempotency key and the deadline. Compiling a graph is cheap; getting the identity
    wrong is not.
    """
    return create_agent(
        model=build_model(settings),
        tools=_tools(customer_id, settings, idempotency_key, deadline),
        system_prompt=_system_prompt(),
        middleware=[
            # A runaway loop is one bad tool description away. 'end' stops the run and
            # keeps whatever the agent had; 'continue' refuses further tool calls.
            ModelCallLimitMiddleware(run_limit=settings.max_model_calls, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=settings.max_tool_calls, exit_behavior="continue"),
            # Retry what is transient — a dropped connection, a 5xx, a 429 — with backoff and
            # jitter. Never a timeout: that means the model is busy, and asking again makes
            # it busier. Model calls only; the tool that writes is never retried by anything.
            ModelRetryMiddleware(max_retries=settings.model_max_retries, retry_on=_transient,
                                 on_failure="error", initial_delay=1.0, backoff_factor=2.0),
        ],
        checkpointer=checkpointer,
    )


# ---------------------------------------------------------------------------------------
# where conversations live
# ---------------------------------------------------------------------------------------


def open_checkpointer(settings: Settings):
    """The blocking store, for `answer()`. Postgres if `PORTER_POSTGRES_DSN` is set,
    otherwise a SQLite file with WAL on — several threads read and write it at once."""
    if settings.postgres_dsn:
        from langgraph.checkpoint.postgres import PostgresSaver

        saver = PostgresSaver.from_conn_string(settings.postgres_dsn).__enter__()
        saver.setup()
        return saver
    con = sqlite3.connect(settings.checkpoint_path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(con)


@asynccontextmanager
async def open_async_checkpointer(settings: Settings):
    """The same store, reached the async way — what the web process holds open for its
    whole life. `SqliteSaver` raises on every `a*` method, so this is a second class, not a
    flag."""
    if settings.postgres_dsn:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
            await saver.setup()       # creates the tables on first run; safe to repeat
            yield saver
        return
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
        await saver.setup()
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        yield saver


# ---------------------------------------------------------------------------------------
# the entry points
# ---------------------------------------------------------------------------------------


def _collect(result: dict[str, Any], thread_id: str, usage: dict, seconds: float,
             settings: Settings) -> Answer:
    """Turn the graph's final state into the typed answer the contract promises."""
    messages = result.get("messages", [])
    # Only this turn's messages count: the state carries the whole conversation.
    last_human = max((i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"), default=-1)
    turn = messages[last_human + 1:]

    reply, tools_used, model_calls, stopped = "", [], 0, False
    for m in turn:
        if getattr(m, "type", None) != "ai":
            continue
        # A limit that ends the run writes its own message into the conversation, and that
        # message has no usage on it because no model produced it. Counting it as a model
        # call — and showing it as the reply — was a bug: the customer read
        # "Model call limits exceeded: run limit (1/1)".
        if not getattr(m, "usage_metadata", None):
            stopped = True
            continue
        model_calls += 1
        for call in getattr(m, "tool_calls", []) or []:
            tools_used.append(call["name"])
        if getattr(m, "content", ""):
            reply = m.content if isinstance(m.content, str) else str(m.content)
    if stopped:
        reply = LIMIT_REPLY

    totals = {"input_tokens": 0, "output_tokens": 0}
    for counts in usage.values():
        totals["input_tokens"] += counts.get("input_tokens", 0)
        totals["output_tokens"] += counts.get("output_tokens", 0)

    return Answer(
        thread_id=thread_id,
        reply=reply.strip(),
        tools_used=tools_used,
        usage=Usage(
            input_tokens=totals["input_tokens"],
            output_tokens=totals["output_tokens"],
            model_calls=model_calls,
            tool_calls=len(tools_used),
            seconds=round(seconds, 3),
        ),
        stopped_at_limit=stopped,
    )


def answer(question: str, customer_id: float, thread_id: str | None = None, *,
           settings: Settings | None = None, checkpointer=None,
           idempotency_key: str | None = None, deadline: float | None = None) -> Answer:
    """Answer one question, blocking. Everything it needs is an argument."""
    settings = settings or get_settings()
    thread_id = thread_id or f"t-{uuid.uuid4().hex[:12]}"
    owns_checkpointer = checkpointer is None
    checkpointer = checkpointer or open_checkpointer(settings)

    agent = build_agent(customer_id, settings, checkpointer, idempotency_key, deadline)
    config = {"configurable": {"thread_id": thread_id}}
    t0 = time.perf_counter()
    try:
        with get_usage_metadata_callback() as usage:
            result = agent.invoke({"messages": [{"role": "user", "content": question}]}, config)
        return _collect(result, thread_id, usage.usage_metadata, time.perf_counter() - t0, settings)
    finally:
        if owns_checkpointer and hasattr(checkpointer, "conn"):
            checkpointer.conn.close()


async def aanswer(question: str, customer_id: float, thread_id: str | None = None, *,
                  settings: Settings | None = None, checkpointer=None,
                  idempotency_key: str | None = None, deadline: float | None = None) -> Answer:
    """The same answer, awaited — so a caller that runs out of time can cancel it.

    Cancelling this coroutine cancels the model call in flight and stops the graph before
    its next step. It cannot un-start a tool that is already running, which is why the tool
    that writes checks the deadline before it starts.
    """
    settings = settings or get_settings()
    thread_id = thread_id or f"t-{uuid.uuid4().hex[:12]}"
    if checkpointer is None:
        async with open_async_checkpointer(settings) as saver:
            return await aanswer(question, customer_id, thread_id, settings=settings,
                                 checkpointer=saver, idempotency_key=idempotency_key,
                                 deadline=deadline)

    agent = build_agent(customer_id, settings, checkpointer, idempotency_key, deadline)
    config = {"configurable": {"thread_id": thread_id}}
    t0 = time.perf_counter()
    with get_usage_metadata_callback() as usage:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]}, config)
    return _collect(result, thread_id, usage.usage_metadata, time.perf_counter() - t0, settings)

