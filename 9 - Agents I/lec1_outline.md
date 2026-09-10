```
START — one business question lands on your desk:
“What was our total revenue, excluding cancelled orders?”
  │
  ▼
[P0] Prepare the world the agent will work in
  │
  ├── UCI Online Retail ── 541,909 transaction line items
  ├── Reshape the spreadsheet into a SQLite database
  │     ├── invoices ──── invoice number · customer · country · cancellation flag
  │     ├── products ──── stock code · product description
  │     └── line_items ── invoice number · stock code · quantity · unit price
  ├── Invoice number starts with “C”? → is_cancelled = 1
  └── Configure the models — plain OpenAI SDK, no agent framework
        ├── gpt-4.1-nano ─────────── worker: tool requests and SQL generation
        ├── gpt-4.1-mini ─────────── separate reviewer
        └── text-embedding-3-small ─ memory retrieval
  │
  ▼
[P1] Ask the bare LLM — no tools, no database access
  │
  ├── Ask it to reason step by step and give a single-number estimate
  ├── Saved reply invents order counts, cancellation rate, and average order value
  └── A plausible calculation appears — but its inputs never came from our data
        → reasoning cannot supply facts the model cannot access
  │
  ▼
[P2] Close the ACTION gap — give the model database tools
  │
  ├── list_tables() ───── discover available tables
  ├── get_schema(table) ─ inspect columns, types, and sample rows
  └── run_sql(query) ──── execute SQL through a read-only connection
        ├── successful query → column names and result rows
        └── failed query → SQL ERROR returned as text
              → the model can read the failure and attempt a correction
  │
  ▼
[P2] Describe the tools and drive the protocol by hand
  │
  ├── Each Python function gets a JSON description
  │     └── name · purpose · argument schema
  ├── Model returns a tool name and JSON arguments
  ├── Python dispatches the request to the actual function
  └── Append the assistant request and matching tool result
        └── tool_call_id connects each result to the request it answers
  │
  ├── Manual example: “How many invoices are cancelled?”
  │     └── table discovery → schema inspection → SQL → answer
  │           (the notebook hand-drives the first two rounds)
  └── The model can request actions, but we still keep the interaction moving
  │
  ▼
[P3] Close the CONTROL gap — automate the loop with run_react()
  │
  ├── Send instructions + user question + conversation history to the LLM
  │
  ├── TOOL CALLS requested?
  │     ├── parse the arguments for EVERY requested call
  │     ├── Python executes the functions
  │     ├── append every result with its matching tool_call_id
  │     └── send the updated conversation back to the LLM
  │           → observe what happened → choose the next action → repeat
  │
  ├── NO TOOL CALLS requested?
  │     └── return the assistant's answer or explanation of the limitation
  │
  └── Still requesting tools after the step budget?
        └── stop with a max_steps message — default budget: 8 model calls
  │
  ▼
[P3] Run the revenue question against the actual database
  │
  ├── discover tables → inspect schema → generate SQL → read the result
  ├── exclude invoices where is_cancelled = 1
  └── saved answer: 10,644,560.42
        → the number now comes from executed SQL
  │
  ▼
[P3] Probe the agent's limits
  │
  ├── Request a nonexistent profit_margin column
  │     └── inspect schema → encounter the error → report the missing data
  ├── Plant a misleading instruction inside a product description
  │     └── compare the agent's answer with a reference query
  │           → read-only access protects the database, not answer correctness
  └── Track tokens sent at every step
        └── each call resends the growing history → later steps cost more
  │
  ▼
[P4] REASONING — compare ways to organize the work
     These are separate demonstrations, not stages every request must execute.
  │
  ├── Chain-of-Thought ── reason through a response in one call
  │     └── already tried in P1; still needs access to the relevant facts
  │
  ├── Self-Consistency ── sample several answers, then vote
  │     ├── arithmetic example: 40 units × 2.50, minus 8 returned units
  │     ├── collect 7 samples → normalize numeric formatting → count votes
  │     └── saved majority: 80.00
  │           → repeated agreement can still preserve a systematic mistake
  │
  ├── ReAct ── decide one step at a time, using each observation
  │     └── the P3 loop can change direction after a result or error
  │
  ├── Plan-and-Solve ── write a numbered plan before acting
  │     └── pass the plan into run_react() → execute with observations available
  │
  └── ReWOO-style demo ── plan SQL calls upfront using result placeholders
        ├── step 1: find the country with the most invoices
        ├── bind its result to #E1
        ├── step 2: substitute #E1 into the next SQL query
        └── saved run: United Kingdom found → guessed invoice_id column fails
              → fewer planner calls, but this executor has no repair loop
  │
  ▼
[P5] REFLECTION — a query can execute successfully and still be wrong
  │
  ├── Naive query: sum quantity × unit_price across every line item
  │     └── saved result: 9,747,747.93
  ├── Correct query: join invoices and filter is_cancelled = 0
  │     └── saved result: 10,644,560.42
  └── Cancelled invoices contribute −896,812.49
        → their negative quantities lower the naive total
        → execution success does not establish business correctness
  │
  ▼
[P5] Compare five ways to check and improve the SQL
  │
  ├── Self-Refine baseline ── ask the worker to critique the flawed query
  │     ├── use the vague question: “What is our total revenue?”
  │     ├── no verification result or supplied cancellation rule
  │     └── all 4 saved verdicts accept the query
  │           → confident self-approval can preserve the original mistake
  │
  ├── Self-Debug ── let execution errors guide the correction
  │     ├── start with deliberately broken column and join names
  │     ├── execute → return the error and schema → regenerate → execute again
  │     └── saved run repairs the query on attempt 2
  │           → catches execution failures; silent business errors need more
  │
  ├── CRITIC-style check ── gather evidence before judging
  │     ├── run a verification query for revenue in cancelled invoices
  │     └── give that result to the critic alongside the original SQL
  │           → concrete evidence exposes the silently incorrect total
  │
  ├── Judge + Revise ── use a separate reviewer and an explicit rubric
  │     ├── generate SQL → execute → review
  │     ├── check correctness · cancellation rule · plausibility
  │     ├── PASS → finish
  │     └── SQL error or REVISE → feed back the problem → generate again
  │           → cap the loop at 3 rounds
  │
  └── Reflexion ── preserve lessons across attempts
        ├── attempt a France-revenue query → receive critique
        ├── turn the critique into a reusable instruction
        │     “Exclude cancelled invoices in your calculation.”
        └── inject accumulated lessons into the next attempt
              → the saved run passes on trial 2
  │
  ▼
[P6] MEMORY — make useful context available beyond the current attempt
  │
  ├── Short-term ── the conversation list used by the agent loop
  │     ├── windowing: retain the system message and recent turns
  │     └── summarization: compress older turns and retain the recent ones
  │           → compare retained context against token usage
  │
  ├── Semantic ── seed reusable facts about this database
  │     ├── revenue excludes cancelled invoices
  │     ├── returns appear as negative quantities
  │     ├── guest checkouts have NULL customer_id
  │     └── country naming conventions and correct join keys
  │
  ├── Episodic ── store a previous question together with its SQL solution
  │     └── retrieve the P5 query as a worked example for a related question
  │
  └── Procedural ── standing instructions and available operations
        └── AGENT_INSTRUCTIONS · tool schemas · application code
  │
  ▼
[P6] Build MemoryStore — a small store written by hand
  │
  ├── Embed each memory with text-embedding-3-small
  ├── Store:
  │     { text, kind, embedding, importance, last_accessed }
  ├── Retrieve by relevance + recency + importance
  │     ├── relevance ─── cosine similarity to the question
  │     ├── recency ───── decay with time since last access
  │     └── importance ── priority assigned to consequential facts
  └── Refresh last_accessed when a memory is retrieved
        → frequently used memories remain recent
        → this implementation lives in RAM; disk persistence is not implemented
  │
  ▼
══════════════════════ the assembled agent at query time ══════════════════════
  │
  ▼
[P7] A new revenue question arrives
  │
  ▼
[P7] RECALL — retrieve relevant context
  ├── 2 semantic memories: applicable rules
  └── 1 episodic memory: a similar previous question and query
  │
  ▼
[P7] Add recalled memories to the agent's instructions
  │
  ▼
[P7] REACT — run the existing tool loop
  ├── inspect schema → write SQL → execute → observe
  ├── use results and errors to choose the next action
  └── finish when the model stops requesting tools, or the step budget runs out
  │
  └── REFLECT within the loop:
        observations can guide corrections on the next pass
        (the separate P5 judge is not called by insight_agent())
  │
  ▼
[P7] Produce the answer
  │
  ▼
[P7] REMEMBER — prepare an episode for future questions
  ├── nonempty answer without max_steps?
  │     ├── NO → return without storing an episode
  │     └── YES → generate SQL again from the question and answer
  └── regenerated SQL starts with SELECT?
        ├── YES → store the question and regenerated SQL
        └── NO → skip storage
              → this SQL is newly generated, not captured from the executed call
              → the current code does not verify it before storage
  │
  ▼
[P7] Return the answer — retained memories are available to the next question
  │
  ├── “What is our total revenue?”
  │     ├── recalled rule supplies the omitted cancellation condition
  │     └── saved numerical answer: 10,644,560.42
  │
  └── “What is the total revenue for France, excluding cancelled orders?”
        ├── recalled rules and a previous query support the variation
        └── saved numerical answer: 209,715.11
  │
  ▼
END RESULT — InsightAgent, built by hand:
an analyst that can discover the schema, request and execute SQL,
use observations to recover from errors, and reuse rules and past queries.

Habits carried through P0–P7
  • inspect schemas and check tools directly before relying on generated SQL
  • answer every tool call, return readable errors, and cap retries
  • verify business rules even when execution succeeds
  • treat tool results and recalled memories as untrusted input
  • measure correctness across known-answer questions, alongside token cost
  • preserve verified facts and useful lessons
  • use a fixed workflow when the necessary steps are already known
```