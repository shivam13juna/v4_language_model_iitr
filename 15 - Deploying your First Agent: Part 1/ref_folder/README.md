# Porter — shipping an agent

**An agent that works in a notebook, turned into a service customers can use, deployed on a server.**

---

## What we're trying to do

- **The agent:** Porter, the order desk of Wickmere & Rook, an online gift shop. A customer asks
  about their own orders, and Porter looks them up in the shop's database and answers. It has
  three tools: `look_up_order`, `price_order`, and `start_return`, which opens a return request
  but can't pay it.
- **Where it starts:** an agent that answers correctly in a notebook, for one person, in one
  kernel. As soon as anybody else uses it, nine things break (the table below).
- **Where it ends:** the same agent as a web service on an AWS server, over HTTPS. Many customers
  use it at once, each signed in and seeing only their own orders. Conversations survive a
  restart, and a refund waits for a member of staff and is paid once.
- **Four routes, all written in the notebook:** `POST /v1/chat` answers a question, in one reply
  once the whole answer is ready; `GET /v1/threads/{id}` shows one of your conversations;
  `POST /v1/returns/{id}/approve` lets staff pay a return; `GET /healthz` checks the database, the
  model and the ledger.
- **Two pages to use it:** the order desk at `/`, where a customer sees their orders and chats with
  Porter, and the returns desk at `/staff`, where staff approve or reject refunds. "Using the app",
  below, shows both.

## How we're doing it

**One notebook writes the whole service, one problem at a time. Each Part makes its problem
happen, fixes it, and runs the same check again.**

P0 sets up a checker, the model (OpenAI's `gpt-4.1-mini` by default, or `gpt-oss:20b` in Ollama),
and the agent run the way a notebook usually runs it. It ends with what breaks:

| Part | what breaks once anybody else uses it | the fix |
|---|---|---|
| P1 | it leans on whatever the kernel happens to hold | settings from the environment, tools bound to one customer, one function: `answer()` |
| P2 | nothing checks what comes in, and every failure looks different | a typed web API (FastAPI), one shape for every error, a health check |
| P3 | anyone can be anyone, and read anyone's conversation | a signed token decides who the customer is; your own conversations only |
| P4 | a restart forgets every conversation | conversations saved outside the process: SQLite, then Postgres |
| P5 | one slow request stalls every other one | the run is awaited instead of blocking, so everyone else keeps being served |
| P5 | a timeout tells the caller "failed" while the work carries on | the timeout cancels the run; a ceiling on model and tool calls |
| P6 | a retry pays a refund twice, and nothing asks a person first | the agent only opens a request; staff approve it; the same request twice changes nothing |
| P7 | it only runs in this notebook, with this Python, on this laptop | the same code as files (`porter/`), in a Docker image |
| P8 | nobody outside this machine can reach it | one AWS server: HTTPS, the container, Postgres, checked from outside |

- **P0–P6 happen inside the notebook.** A fix redefines one function the running app calls by
  name (`identify()` in P3, `open_async_checkpointer()` in P4), so the same failing call runs
  again, and now passes.
- **P7 and P8 use the files.** `porter/` is the code the notebook wrote, split into modules. P7
  builds it into an image; P8 deploys that image.

---

## How to go through this folder

**Open `v6_workshop_1_shipping_an_agent.ipynb` and run it top to bottom.** P0–P6 need nothing else
open. The rest of the folder is the service as files (`porter/`), and what you run in a terminal
when the notebook reaches P7 or P8.

| Parts | what has to be running | what you type in a terminal |
|---|---|---|
| P0–P6 | a model: an OpenAI API key, or Ollama with `gpt-oss:20b` | nothing |
| P7 · the container | + Docker Desktop | three `docker` commands |
| P8 · off the laptop | + an AWS account (and the model tunnel, only with Ollama) | the deploy scripts |

### 1 · Set up, once

```bash
pip install -r requirements-dev.txt
cp .env.example .env              # set PORTER_AUTH_SECRET and OPENAI_API_KEY (the file says how)
python check_setup.py             # ✅ / ❌ for everything, with the fix for each ❌
```

- **The model is one setting.** `PORTER_PROVIDER=openai` (the default) runs `gpt-4.1-mini` on
  OpenAI's API and needs `OPENAI_API_KEY`. `PORTER_PROVIDER=ollama` runs `gpt-oss:20b` in Ollama
  (`ollama pull gpt-oss:20b`, 13 GB, no key). Both serve the Chat Completions API, so nothing
  else changes.
- No OpenAI key, and no room for a 20B model? `PORTER_PROVIDER=ollama` with a shared tunnel's
  URL and token (`ngrok/README.md`).
- `online_retail.db` is already here. If it goes missing, `python prepare_data.py` rebuilds it
  from the UCI download.

### 2 · P0–P6: only the notebook

- Run the cells in order.
- Each Part opens with a picture of what happens in it, drawn with the real names and values.
  The colours are explained once, under "How each Part works".
- Each piece of the service is written in the Part that needs it: settings, tools and agent (P1),
  the FastAPI app (P2), tokens (P3), the conversation store (P4), approvals (P6). Nothing is
  imported from `porter/`.
- What the notebook writes (conversations, ledgers) goes to `.scratch/`, which is emptied at the
  start of every run.
- P7 opens with a table of which function lives in which file, for going through `porter/`
  afterwards.

### 3 · P7: the container

Start Docker Desktop. Then, in a terminal in this folder:

```bash
docker build --build-arg PORTER_VERSION=2.0.0 -t porter:2.0.0 .   # the image P7's cells run
docker compose up --build        # Porter + Postgres, when the notebook asks; leave it running
docker compose restart porter    # when the notebook asks for a restart
```

- The same two pages are at <http://localhost:8080/> and <http://localhost:8080/staff> while the stack
  runs ("Using the app", below).
- `docker compose down` when you're finished. Conversations stay in the Postgres volume.

### 4 · P8: off the laptop

First:

- **AWS credentials:** `aws configure`; then `aws sts get-caller-identity` prints your ARN.
- **Only if the deployment's provider is Ollama:** a free static domain from ngrok, in `.env` as
  `NGROK_DOMAIN`, and a long random `PORTER_MODEL_TOKEN` for the tunnel to check.
  `ngrok/README.md` walks through both. With OpenAI, the instance calls OpenAI directly.

Then, in a terminal, in the order the notebook uses them:

```bash
./ngrok/start_model_tunnel.sh      # Ollama only: this laptop's Ollama on a public URL; leave it running
./deploy/aws/create.sh             # one t4g.small instance, a few cents an hour until destroy.sh
cp .env.prod.example .env.prod     # then fill it in: the server's secret and its OpenAI key
./release.sh 2.0.0                 # selfcheck + smoke suite, inside the image that ships
./deploy/aws/push.sh 2.0.0         # copy up, build on the instance, start
```

- The notebook then checks it from outside: HTTPS, a 401 without a token, an answer, and a
  conversation that survives a restart on the instance (the ssh command is in the notebook).
- The two pages are at `https://<your site>/` and `https://<your site>/staff`. Demo sign-in is off
  there, so sign in with tokens made with the server's secret ("Signing in", below).
- `DEPLOY.md` has every step in more detail, and what to do when one fails.

### 5 · When you're finished

- `./deploy/aws/destroy.sh` removes the instance, and the cost stops.
- Stop the tunnel, if you started one, with Ctrl+C.

### Saved runs: `RUN_LIVE`

- The P7 and P8 cells that need a container, the tunnel or the instance save what they measure
  in `results/`.
- `RUN_LIVE = False`, the setup cell's default, shows the saved run instead. `True` measures yours.

---

## Using the app

**Two pages sit on top of the service: the order desk for customers, and the returns desk for
staff. Open both side by side and you can show the whole flow, from a customer's question to a
paid refund.**

![The order desk: a customer's orders on the left, the conversation with Porter on the right](docs/order_desk.png)

### Start it

```bash
uvicorn porter.api:app --port 8080       # just the service, from this folder
# or: docker compose up --build          # the service and Postgres, the way P7 runs it
```

- The model key comes from `OPENAI_API_KEY` in `.env`, or from your shell's environment: an
  exported value wins over `.env`.
- After changing `.env` or anything in `porter/`, stop the server (Ctrl+C) and start it again.

| page | address | who it is for |
|---|---|---|
| the order desk | <http://127.0.0.1:8080/> | a customer: their orders, and a chat with Porter |
| the returns desk | <http://127.0.0.1:8080/staff> | staff: the return requests, with Approve and Reject |
| the API | <http://127.0.0.1:8080/docs> | trying the routes by hand |

Open them as `127.0.0.1` or `localhost`, or over HTTPS.

### Signing in

- **Every request carries a sign-in token:** a short signed note that says who you are,
  "customer 12381" or "staff alice", valid for 8 hours. The service signs it with
  `PORTER_AUTH_SECRET` and believes it, never a customer number typed into a form. A customer token
  opens the order desk; a staff token opens the returns desk.
- **On your laptop, demo accounts sign you in.** With `PORTER_DEV_LOGIN=true` (the `.env.example`
  default), the sign-in card offers customers 12381 (Norway, 6 orders) and 12490 (France, 10
  orders), any other customer number, and staff member alice. Clicking one asks `POST /dev/token`
  for a token.
- **On a deployment, demo sign-in is off:** `compose.prod.yaml` sets it to false, and Caddy blocks
  `/dev/`. Make a token with the server's secret, and paste it under "Have a token? Paste it":

  ```bash
  PORTER_AUTH_SECRET=<the server's secret> python -m porter.auth customer 12381
  PORTER_AUTH_SECRET=<the server's secret> python -m porter.auth staff alice
  ```

  Without `PORTER_AUTH_SECRET=…` in front, the command signs with your `.env`'s secret: a token
  that only your laptop accepts.
- In a real shop, the shop's own login page would hand the browser this token after checking a
  password. The demo accounts and the paste box stand in for that.

![The sign-in card, with the demo accounts](docs/sign_in.png)

### What you'll see: the whole flow in two minutes

1. **The order desk, as customer 12381.** Their six orders on the left, newest first (C565050 is a
   cancellation). Porter greets you with three questions about these orders. Ask "How much was
   order 580638?": the reply says 147.01, and under it is the tool Porter used ("Priced the
   order"). "details" shows the time, the model calls and the tokens.
2. **A return.** Ask "Order 574694 arrived damaged. I'd like to return it." Porter opens a return
   request, a note says a colleague reviews it first, and order 574694's card now says "Return
   requested". Nothing has been refunded.
3. **The returns desk, as alice.** Open `/staff` in a second tab. The request is under "Waiting":
   £419.06, order 574694, customer 12381, and the reason. "Approve refund" asks you to confirm the
   amount; "Yes, pay it" pays refund `F-…`, and the request moves to "Refunded".
4. **Back on the order desk,** the card says "Refunded £419.06". The list refreshes when you come
   back to the tab.
5. **Another customer can't see it.** Sign out, sign in as 12490, and ask about order 580638:
   "There is no order 580638 on this account." The token decides whose orders Porter can reach.

![The returns desk: one request waiting, with Approve refund and Reject](docs/returns_desk.png)

- **Approving twice pays once.** The page hides the buttons once a request is decided, but the
  route is safe on its own: send the same approval again from `/docs` and the same refund comes
  back, marked `replayed`.
- The pages keep your sign-in in the browser until "Sign out". The customer and staff sign-ins are
  kept apart, so one browser can have both desks open.

---

## What's in this folder

| needed for | files |
|---|---|
| every step | the notebook · `porter/` (the service) · `online_retail.db` · `.env` |
| setup | `requirements.txt` · `requirements-dev.txt` · `check_setup.py` · `.env.example` · `prepare_data.py` and `data/` |
| P7 | `Dockerfile` · `.dockerignore` · `compose.yaml` · `Dockerfile.naive` (the usual first Dockerfile, measured against the real one) |
| P8 | `ngrok/` · `deploy/aws/` · `.env.prod.example` · `compose.prod.yaml` · `Caddyfile` · `release.sh` and `evals/smoke.jsonl` · `DEPLOY.md` |
| reference | `DATA.md` (how the data was built, and what it doesn't hold) · `results/` (saved runs) · `docs/` (this README's screenshots) · `.github/` (the model-free checks, as CI) |
| regenerating the notebook | `v6_workshop_1_build_nb.py` · `_check_before_rebuild.py`. Not needed to go through it. |

---

## The service

### Test it

```bash
pytest porter/tests -q                                   # no model, a few seconds
python -m porter.evals --suite smoke --in-process        # the model, under a minute
./release.sh 2.0.0                                       # selfcheck + smoke, inside the image
```

### The shape of it

```
   POST /v1/chat                    a question, a typed answer; the caller waits ≤ request_timeout_s
   GET  /v1/threads/{id}            one of *your* conversations
   GET  /v1/returns                 staff: the return requests
   POST /v1/returns/{id}/approve    staff: pay one — safe to send twice
   POST /v1/returns/{id}/reject     staff
   GET  /healthz                    database, model, ledger, conversation store
   GET  /version                    which version is answering (also on every response header)
   GET  /v1/me                      who a token belongs to: a customer or staff
   GET  /v1/orders                  the signed-in customer's orders, each with its return request
   GET  /metrics                    Prometheus format; nothing in this folder reads it
   GET  /  ·  /staff                the order desk and the returns desk (porter/web/)
```

```
  Meter ─ Gate (size · quota · screens · cache · budget)
     └─ api.py ── who (token) · which (namespaced thread) · how long (cancellable deadline)
          └─ agent.py ── create_agent + limits + retries ── model (OpenAI, or Ollama / tunnel)
               └─ tools.py ── look_up_order · price_order  → online_retail.db (read-only)
                            └ start_return ─► ledger.py: a pending request. Staff pay it.
```

| file | what it does | the notebook writes it in |
|---|---|---|
| `porter/settings.py` | every setting, from the environment; refuses to start on a bad combination | P1 |
| `porter/agent.py` | `answer()`, `aanswer()`; the model, the limits, the retry policy, the conversation store | P1 · P2 · P4 |
| `porter/tools.py` | three tools, closed over one customer, one request and one deadline; and the order list the order desk shows | P1 |
| `porter/schemas.py` | the typed request, answer and refund | P1 · P2 · P6 |
| `porter/api.py` | the only file that knows there is a web | P2 · P6 |
| `porter/auth.py` | signed tokens → an identity; staff vs customer | P3 · P6 |
| `porter/ledger.py` | return requests and refunds; the approval policy is in its schema | P1 · P6 |
| `porter/health.py` · `selfcheck.py` | dependency checks, with and without a server | P2; `selfcheck` runs in P7 |
| `porter/scripted.py` | a stand-in model that follows a script, so the tests need no model | — (P7 runs the tests) |
| `porter/evals.py` | the evaluation runner and scorer | — (`release.sh` runs its smoke suite in P8) |
| `porter/web/` | the order desk (`index.html`), the returns desk (`staff.html`), and the stylesheet and script they share | — |
| `porter/prompts/` | the system prompt, one file per version | P1 writes `v1`, the one the service runs |
| `porter/ops.py` | the operations middleware: size, quota, screens, cache, budget, the run log | — (every switch is off) |
| `porter/obs.py` | JSON logs, tracing, Prometheus metrics | — (the image turns JSON logs on; nothing here uses the rest) |
| `porter/security.py` · `cost.py` · `release.py` | screens, a token bucket, routing, a cache, budgets, prompt versions | — (switched off) |

### Configuration

Every field of `porter/settings.py` is an environment variable with a `PORTER_` prefix;
`.env.example` lists the ones you set. The operations switches (`Ops`) are all off by default.

- **The model:** `PORTER_PROVIDER` (`openai` or `ollama`) fills in the model, the address and the
  key. `PORTER_MODEL`, `PORTER_BASE_URL` and `PORTER_API_KEY` override one value each.
- **The OpenAI key is `OPENAI_API_KEY`,** without the prefix: the name OpenAI's own tools read.
  It is only ever sent to OpenAI's address.

### What it does not do

- **It has no shipping information.** The data does not contain any; the desk says so
  (`DATA.md`).
- **On OpenAI, every request is billed to the key,** whoever sends it, and questions and order
  rows go to OpenAI. The sign-in is what stands between the public address and the bill.
- **On Ollama, the deployed model is a laptop.** The EC2 deployment reaches it through a tunnel;
  it is up while the laptop is up, and one GPU serves every caller.
- **Screens, rate limits, the cache and budgets are in the code and switched off.** Every
  operations switch in `Ops` defaults to off.

*Dataset: UCI Online Retail — Chen, D. (2012), [archive.ics.uci.edu/dataset/352](https://archive.ics.uci.edu/dataset/352/online+retail), CC BY 4.0.*
