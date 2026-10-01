"""Porter itself: the agent for one request, where conversations are kept, and one function that
asks it a question.

    from porter.agent import answer
    first = answer("When did I place order 574694?", customer_id=12381)
    answer("How much was it?", customer_id=12381, thread_id=first["thread_id"])   # the same conversation
"""

import uuid

import openai          # the model client's error types; _transient() sorts them into retry and don't retry
import psycopg         # the Postgres client
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware
from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from porter.settings import settings
from porter.tools import build_tools

# The model's instructions, sent at the start of every conversation.
SYSTEM_PROMPT = """You are Porter, the order desk for Wickmere & Rook, an online gift shop.

You are talking to a customer about their own orders. Be brief and plain — two or three
sentences, no lists unless you are itemising an order.

How to work:
- Any question about an order goes through a tool. Never state a date, a country, a price
  or a total that a tool did not just give you.
- `look_up_order` for where an order is and what happened to it.
- `price_order` for anything about money. Do not add prices up yourself.
- `start_return` only when the customer has clearly said they want to send something back.
  The refund amount comes from the order, never from what the customer says it was.
- If a tool says there is no such order on this account, say exactly that. Do not guess at
  a different order number.
- If you are asked something that is not about this customer's orders, say it is not
  something the order desk can help with."""

def build_model(settings):
    """The model the settings name: the same client for OpenAI and Ollama, since both serve Chat Completions."""
    return ChatOpenAI(model=settings.model, base_url=settings.base_url, api_key=settings.api_key,
                      temperature=settings.temperature,
                      timeout=settings.model_timeout_s,     # one call's budget
                      max_retries=0)                         # retrying is the middleware's job

def _transient(exc):
    """Worth another attempt: a dropped connection, a 5xx, a 429. A timeout is not."""
    # In the openai package a timeout is a kind of connection error, so it is ruled out first.
    if isinstance(exc, openai.APITimeoutError):
        return False
    return isinstance(exc, (openai.APIConnectionError, openai.InternalServerError, openai.RateLimitError))

def build_agent(customer_id, settings, checkpointer, idempotency_key=None, deadline=None):
    """One request's agent: the model, this customer's tools, the rules, three limits, and the store."""
    return create_agent(
        model=build_model(settings),
        tools=build_tools(customer_id, settings.db_path, settings.ledger_path, idempotency_key, deadline),
        system_prompt=SYSTEM_PROMPT,
        middleware=[                                                        # the ceilings
            # At most 6 model calls. At the limit the run ends, keeping what it has so far.
            ModelCallLimitMiddleware(run_limit=settings.max_model_calls, exit_behavior="end"),
            # At most 8 tool calls. Past that, each tool call is refused and the model is told why.
            ToolCallLimitMiddleware(run_limit=settings.max_tool_calls, exit_behavior="continue"),
            # A failed model call is tried again twice (after 1 s, then 2 s) if _transient() says
            # it's worth it. If every attempt fails, the error is raised for the route to report.
            ModelRetryMiddleware(max_retries=settings.model_max_retries, retry_on=_transient,
                                 on_failure="error", initial_delay=1.0, backoff_factor=2.0),
        ],
        checkpointer=checkpointer,                                          # where the conversation is kept
    )


def open_checkpointer(settings):
    """Where conversations are kept between questions: Postgres when PORTER_POSTGRES_DSN is set, as
    the Compose files set it; otherwise this process's memory, gone when the process stops."""
    if not settings.postgres_dsn:
        return InMemorySaver()
    # One connection, with the settings PostgresSaver's own from_conn_string() would give it.
    conn = psycopg.connect(settings.postgres_dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()                      # creates its tables the first time; does nothing after that
    return saver


# One store for the whole process, opened when it starts and shared by every request.
checkpointer = open_checkpointer(settings)


def answer(question, customer_id, thread_id=None):
    """Ask Porter one question, as one customer: the reply, the tools it used, and the tokens.
    The same thread_id carries on a conversation; without one, a new conversation starts."""
    thread_id = thread_id or uuid.uuid4().hex[:12]
    agent = build_agent(customer_id, settings, checkpointer)
    # Run the agent to the end. The callback adds up the tokens of every model call inside it.
    with get_usage_metadata_callback() as usage:
        result = agent.invoke({"messages": [{"role": "user", "content": question}]},
                              # The key includes the customer: the same thread_id from another
                              # customer is another conversation.
                              {"configurable": {"thread_id": f"{customer_id}:{thread_id}"}})
    # result["messages"] is the whole conversation; this question's part starts at its own message.
    last_question = max(i for i, m in enumerate(result["messages"]) if m.type == "human")
    this_turn = result["messages"][last_question:]
    tokens = usage.usage_metadata.values()                     # one entry per model used
    return {
        "thread_id": thread_id,
        "reply": result["messages"][-1].content,
        "tools_used": [call["name"] for m in this_turn for call in getattr(m, "tool_calls", [])],
        "input_tokens": sum(t["input_tokens"] for t in tokens),
        "output_tokens": sum(t["output_tokens"] for t in tokens),
    }
