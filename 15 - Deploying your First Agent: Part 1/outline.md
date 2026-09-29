```
START — Porter works as an agent inside one Jupyter kernel
  │
  │   customer asks: “How much was order 580638?”
  │
  ▼
[P0] PROVE THE AGENT WORKS
  │
  ├── Load the Online Retail data
  │     ├── invoices
  │     ├── line_items
  │     └── products
  │
  ├── Choose where the model runs
  │     ├── OpenAI → gpt-4.1-mini
  │     └── Ollama → gpt-oss:20b
  │
  ├── create_agent(model + look_up_order)
  │     └── model → tool → database → model → answer
  │
  └── Result: correct answers...
        but only for one person, in this running notebook
  │
  ▼
══════════════ turning an agent into a service ══════════════
  │
  ▼
[P1] REMOVE EVERYTHING THE NOTEBOOK WAS SECRETLY PROVIDING
  │
  ├── Settings
  │     └── config comes from typed PORTER_* / .env values
  │         instead of globals and previous cells
  │
  ├── build_tools(customer_id, ...)
  │     ├── look_up_order ─┐
  │     ├── price_order ───┼──► online_retail.db · READ ONLY
  │     └── start_return ──┘
  │              │
  │              └────────► ledger · the service's writable state
  │
  ├── tools CLOSE OVER customer_id
  │     └── the model cannot ask for another customer's data
  │
  ├── build_agent(...)
  │     └── model + customer-scoped tools + execution limits
  │
  └── answer(question, customer_id, thread_id)
        └── one typed result:
            { reply, tools_used, usage, thread_id, ... }
  │
  ▼
[P2] GIVE THE AGENT AN HTTP CONTRACT
  │
  ├── FastAPI wraps answer()
  │
  ├── POST /v1/chat
  │     └── ChatRequest validates input BEFORE the model runs
  │
  ├── GET /v1/threads/{id}
  │     └── retrieve a conversation
  │
  ├── GET /healthz
  │     └── actually checks:
  │          database · model endpoint · ledger
  │
  └── every failure gets one predictable shape
        { error, detail, request_id }
        ├── 422 invalid_request
        ├── 404 thread_not_found
        ├── 503 model_unavailable
        ├── 504 timeout
        └── 500 internal
  │
  │   BUT...
  │   the first identify() still trusts customer_id from the request body
  │
  ▼
[P3] STOP TRUSTING THE CALLER'S CLAIMED IDENTITY
  │
  ├── login system issues a signed JWT
  │       { sub: customer_id, role, exp }
  │
  ├── request sends
  │       Authorization: Bearer <token>
  │
  ├── identify()
  │     ├── signature valid?
  │     ├── token expired?
  │     ├── correct role?
  │     └── body customer_id agrees with token?
  │
  ├── customer identity now comes from the TOKEN
  │     └── never from what the caller typed
  │
  └── threads are namespaced by customer
        customer 12381 → c12381:t-...
        customer 12490 → c12490:t-...

        another customer's thread therefore looks like:
        → 404 thread_not_found
        rather than leaking that the thread exists
  │
  ▼
[P4] MOVE CONVERSATION MEMORY OUTSIDE THE PROCESS
  │
  │   before:
  │   InMemorySaver
  │       └── process dies → conversation dies
  │
  ├── open_async_checkpointer()
  │     ├── local/simple → AsyncSqliteSaver
  │     └── deployed     → AsyncPostgresSaver
  │
  └── now the architecture becomes:
        ┌─────────────────────────────┐
        │ Porter process             │  ← disposable / stateless
        └─────────────┬───────────────┘
                      │ load / save
                      ▼
        ┌─────────────────────────────┐
        │ conversation store          │  ← persistent / stateful
        │ SQLite or Postgres          │
        └─────────────────────────────┘

        restart Porter
             ↓
        “And when was that one placed?”
             ↓
        previous conversation still exists
  │
  ▼
[P5] MAKE TIME A FIRST-CLASS PART OF THE SERVICE
  │
  ├── DON'T:
  │     async route → blocking answer()
  │       └── one slow model call freezes the event loop
  │
  ├── DO:
  │     async route → await aanswer()
  │       └── other requests + /healthz can keep running
  │
  ├── put ceilings around the agent
  │     ├── maximum model calls
  │     ├── maximum tool calls
  │     └── model-call timeout
  │
  ├── put a timeout around the whole request
  │
  │       await asyncio.wait_for(
  │           aanswer(...),
  │           timeout=request_timeout
  │       )
  │
  ├── timeout → CANCEL the awaited run
  │     └── don't tell the caller “failed”
  │         while work secretly continues
  │
  └── retry only failures that can plausibly improve
        ├── network failure / 5xx / 429 → retry with backoff
        ├── model timeout → don't blindly retry
        └── writes → never rely on retries for correctness
  │
  ▼
[P6] MAKE WRITES SAFE
  │
  │   reading an order twice is harmless
  │   paying a refund twice is not
  │
  ├── customer tells Porter:
  │     “Order 574694 arrived broken”
  │
  ├── agent may call:
  │       start_return(...)
  │
  │       ↓
  │
  │     ledger.returns
  │       status = pending
  │
  │     NO MONEY HAS MOVED
  │
  ├── Idempotency-Key accompanies the request
  │     └── same request sent twice → one return request
  │
  ├── agent has NO refund tool
  │
  └── a STAFF member separately calls
        POST /v1/returns/{id}/approve
                  │
                  ▼
             approve(...)
                  │
                  ├── verify pending return
                  ├── create refund
                  ├── mark return approved
                  └── commit atomically

        database UNIQUE constraints guarantee:
        ├── same return request twice → one request
        ├── same approval twice → one refund
        └── one order → at most one refund
  │
  ▼
════════════════════════════ request time ════════════════════════════
  │
  ▼
Customer asks a question
  │
  ▼
POST /v1/chat
  │
  ├── validate request [P2]
  │
  ├── verify signed identity [P3]
  │
  ├── locate/create that customer's thread [P2/P4]
  │
  ▼
Build an agent specifically for that customer [P1]
  │
  ├── model
  │
  ├── conversation checkpointer
  │
  └── customer-scoped tools
        ├── look_up_order
        ├── price_order
        └── start_return
  │
  ▼
Model decides whether a tool is needed
  │
  ├── reads → read-only business database
  │
  └── return request → idempotent ledger write [P6]
  │
  ▼
Model receives tool result and writes the response
  │
  ├── execution remains bounded [P5]
  ├── conversation is persisted [P4]
  └── usage + tools used are collected
  │
  ▼
HTTP 200
{
  thread_id,
  reply,
  tools_used,
  usage,
  ...
}
  │
  ▼
END RESULT

What began as:

    model + Python function + notebook memory

has become:

    authenticated HTTP service
          +
    customer-isolated agents
          +
    persistent conversations
          +
    bounded asynchronous execution
          +
    idempotent human-approved writes
          +
    health checks and predictable failures
          +
    reproducible Docker package
          +
    HTTPS deployment on AWS
```