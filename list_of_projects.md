

## 1) Build a support-ticket resolution copilot

**Interview question:**
“You are given historical customer support tickets, help-center articles, and product documentation. Build a system that helps a support agent answer a new ticket faster, with grounded responses, suggested macros, and escalation signals. How would you design, evaluate, and productionize it?”

### What the learner should build

A web app or API that takes a new support ticket and returns:

* ticket classification: billing / bug / feature request / account / refund / other
* urgency score
* retrieved relevant help-center articles
* a drafted response for the support agent
* whether the ticket should be escalated to engineering or fraud/risk
* a short explanation of why those suggestions were made

### Where to get data

Use a mix of public datasets and synthetic company docs.

**Ticket data**

* Kaggle customer support ticket datasets
* Zendesk-style support datasets on Hugging Face
* e-commerce complaint datasets
* public GitHub issues can also be repurposed as “technical support” tickets

**Knowledge base data**

* scrape docs from a public product site like Stripe, Notion, Vercel, GitHub, Slack, or Shopify docs
* use FAQs from public SaaS help centers
* create 20–30 internal policy docs manually, such as refund rules, SLAs, escalation policy, plan limits

### What the system should do

1. Ingest help articles and policy docs into a retrieval system.
2. Embed and index historical tickets.
3. For a new ticket:

   * classify the intent
   * retrieve similar past tickets
   * retrieve relevant KB articles and policies
   * draft a reply grounded in those sources
   * flag cases where policy uncertainty exists
4. Return citations for every factual statement.

### Skills covered

* OpenAI API usage
* embeddings and RAG
* structured output
* eval/testing
* production concerns: caching, prompt versioning, retries
* optional fine-tuning for classification

### What good evaluation looks like

Measure:

* classification accuracy
* retrieval precision at top-k
* response groundedness
* hallucination rate
* agent acceptance rate on drafted replies
* average latency and cost per ticket

Create a small gold dataset:

* 100 manually labeled tickets
* correct class
* whether escalation is needed
* ideal supporting KB doc IDs
* acceptable answer rubric

### Stretch version

Add a human-in-the-loop flow:

* support agent edits the draft
* system logs corrections
* retrain/fine-tune a small classifier or response ranker later

---

## 2) Build a multi-document research assistant for PDFs

**Interview question:**
“A user uploads 50 PDFs containing annual reports, research papers, policy documents, and meeting notes. Build a system that answers questions with citations, compares documents, and extracts structured facts. How would you handle chunking, retrieval, and evaluation?”

### What the learner should build

An application where a user uploads PDFs and can:

* ask questions with page-level citations
* compare findings across multiple documents
* extract a table of facts, entities, risks, dates, or claims
* generate a summary memo with linked evidence

### Where to get data

Use public PDFs.

**Possible sources**

* arXiv papers
* SEC 10-K filings
* government policy PDFs
* World Bank / IMF / OECD reports
* university lecture notes
* company annual reports
* court judgments or public legal PDFs if desired

Use at least 30–50 PDFs with mixed formatting quality.

### What the system should do

1. Parse PDFs into sections, preserving page numbers and headings.
2. Handle difficult formats:

   * repeated headers/footers
   * tables
   * appendices
   * scanned pages if present
3. Build a RAG pipeline that returns:

   * best matching chunks
   * answer with citations
   * extracted evidence snippets
4. Add a structured extraction mode:

   * for example, “extract all risk factors and their supporting pages”
   * or “list every stated revenue figure with year and page number”

### Skills covered

* embeddings and chunking strategy
* OpenAI API for answer generation
* RAG
* evals
* reliability and long-context handling
* prompt design for extraction

### Where learners usually fail

* chunking without section boundaries
* no citation validation
* retrieving noisy text from headers/footers
* answering from model priors instead of source text

### What good evaluation looks like

Prepare 50 benchmark questions with gold answers and source pages.

Score:

* citation correctness
* answer completeness
* exactness of extracted facts
* retrieval recall
* cross-document comparison quality

A good test case is:
“Which 3 documents mention supply-chain risk, and how do their mitigation strategies differ?”

That forces real retrieval, not keyword matching.

### Stretch version

Add a “claim verification” mode:

* user pastes a claim
* system finds supporting and contradicting evidence across all documents
* outputs verdict: supported / partially supported / unsupported

---

## 3) Build a bug triage and incident assistant for engineering teams

**Interview question:**
“You have bug reports, stack traces, incident postmortems, runbooks, and GitHub issues. Build a system that helps triage new issues, find duplicates, retrieve similar incidents, and suggest the first debugging steps. How would you architect it?”

### What the learner should build

A developer-facing assistant that accepts a bug report or incident description and returns:

* severity level
* likely subsystem
* possible duplicate issues
* similar past incidents
* suggested first debugging steps
* relevant runbook sections
* confidence score

### Where to get data

**Public sources**

* GitHub issues from large repos
* public incident writeups from SaaS companies
* SRE runbooks or engineering troubleshooting docs
* stack traces from synthetic examples
* public bug datasets or software defect reports

**Better setup**
Pick one open-source project with lots of issues:

* Kubernetes
* VS Code
* LangChain
* React
* Django
* Elasticsearch

Then combine:

* GitHub issue text
* release notes
* docs
* troubleshooting pages

### What the system should do

1. Build an issue ingestion pipeline.
2. Cluster old issues semantically using embeddings.
3. For a new issue:

   * classify severity
   * detect duplicates
   * retrieve related issues and docs
   * summarize likely root causes
   * suggest debugging checklist
4. Use structured output so the result is machine-consumable.

### Skills covered

* embeddings
* semantic search and clustering
* RAG
* LangGraph-style multi-step workflow
* evaluation
* production reliability

### What makes this a strong project

It forces learners to deal with ambiguity. A bug report is messy, incomplete, and often wrong. That is far more realistic than a clean Q&A bot.

### What good evaluation looks like

Gold dataset:

* 200 historical issues
* manually labeled subsystem
* severity
* duplicate links if any
* useful runbook references

Metrics:

* duplicate retrieval hit rate
* severity classification accuracy
* top-k retrieval quality
* usefulness of debugging suggestions
* false-confidence rate

### Stretch version

Add an “incident timeline summarizer”:

* input Slack thread / logs / incident notes
* output timeline, root cause hypothesis, impacted systems, action items

That ties directly into production workflows.

---

## 4) Build a sales-call and CRM copilot

**Interview question:**
“A sales team has call transcripts, email threads, CRM notes, and product docs. Build a copilot that summarizes calls, extracts next steps, identifies deal risks, and drafts follow-up emails. How would you make the system accurate and measurable?”

### What the learner should build

A system that takes meeting transcripts or sales conversations and outputs:

* structured summary
* customer pain points
* objections raised
* competitor mentions
* next actions
* deal risk score
* follow-up email draft
* CRM-ready structured fields

### Where to get data

**Public sources**

* meeting transcript datasets
* sales conversation examples from YouTube transcript data
* customer interview transcripts
* public SaaS product docs and pricing pages
* synthetic CRM notes created by learners

If clean sales data is hard to find, simulate it:

* generate 50–100 realistic call transcripts
* define product docs, pricing rules, discount policy, competitor matrix
* use those as the knowledge base

That is fine because the point here is workflow quality and extraction reliability.

### What the system should do

1. Ingest product docs, plan/pricing info, case studies, and objection-handling docs.
2. Accept transcript input.
3. Extract:

   * company size
   * use case
   * current pain points
   * blockers
   * budget/timeline clues
   * next meeting date if mentioned
4. Generate:

   * short summary
   * CRM JSON object
   * tailored follow-up email
5. Flag unsupported claims. Example: if the model says “our enterprise plan supports SSO” it must cite the plan doc.

### Skills covered

* OpenAI API
* structured extraction
* RAG
* evaluation
* business workflow integration
* prompt and schema design

### What good evaluation looks like

Create a gold benchmark of transcripts with manually labeled outputs:

* correct action items
* correct objections
* accurate competitor mentions
* correct product feature references
* correct follow-up style

Metrics:

* extraction precision/recall
* unsupported product claim rate
* follow-up usefulness judged by rubric
* cost and latency

### Stretch version

Add a deal-health dashboard:

* aggregate many calls
* identify common objections
* surface which competitors show up most often
* rank accounts by risk

That becomes both a GenAI project and an analytics system.

---

## 5) Build a personal productivity agent using tools and MCP-style thinking

**Interview question:**
“Build a personal work assistant that can read notes, summarize meetings, find action items, draft follow-ups, and answer ‘what should I do today?’ using documents, calendar, and tasks. How would you design tool use, memory, and safeguards?”

### What the learner should build

A tool-using assistant that works over a user’s productivity data:

* notes
* meeting transcripts
* tasks
* calendar events
* personal docs or wiki pages

Given a question like “What should I focus on today?” it should:

* inspect tasks and deadlines
* read recent meeting notes
* infer priorities
* produce a ranked action list
* draft follow-up messages where needed

### Where to get data

Since private user data is hard to use in a course, create a realistic synthetic dataset.

Build a small workspace:

* 100 task items
* 30 meeting notes
* 20 project docs
* 10 email threads
* 2–3 calendars worth of events
* project status docs with deadlines and owners

You can create this dataset by hand or semi-synthetically.

Alternative sources:

* public meeting notes
* open project management docs
* markdown knowledge bases from GitHub repos
* exported Notion-like sample workspaces

### What the system should do

Tool calls should include things like:

* search notes
* fetch calendar events
* list open tasks
* retrieve project docs
* create a draft follow-up
* store a memory item like “user prefers concise daily plans”

This is where LangGraph or MCP-style architecture becomes useful.

### Skills covered

* agents
* tool orchestration
* stateful workflows
* context management
* retrieval
* production reliability
* practical productivity workflows

### The hard part

The assistant should not just answer questions. It should make decisions in sequence.

Example workflow:

1. user asks: “Prep me for today”
2. agent checks calendar
3. retrieves related docs for each meeting
4. finds unresolved action items from past notes
5. drafts a prep brief
6. proposes follow-up messages for overdue items

### What good evaluation looks like

Use task-based evaluation, not only text quality.

Example benchmark questions:

* what meetings need prep today?
* which action items are overdue?
* what changed in project alpha since last week?
* draft a follow-up to Priya about the API review
* summarize blockers from the last 3 standups

Metrics:

* task completion accuracy
* relevance of retrieved context
* unnecessary tool call count
* latency
* consistency across repeated runs

### Stretch version

Add approval boundaries:

* assistant may draft but not send
* assistant may suggest calendar changes but not apply them
* assistant must show evidence for every recommendation

That makes it much more realistic and teaches guardrails.

---

# Best way to use these projects description

For each one, Try to answer these six things:

### 1. Problem framing

Who exactly is the user? What is the repetitive workflow? What is the measurable win?

### 2. Data plan

What data exists? What must be created? What is noisy? What is the schema?

### 3. System design

What is prompt-only, what is retrieval, what is tool use, what is fine-tuned?

### 4. Evaluation

How do you prove it works? What is the gold dataset? What are the failure cases?

### 5. Production concerns

How will you handle retries, caching, cost control, logging, rate limits, and stale indexes?

### 6. Trust and safety

When should the system abstain, escalate, or require human review?

---

