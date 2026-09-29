# Deploying Porter to AWS

One target, rehearsed end to end: **a single EC2 instance running the same Docker Compose
setup as your laptop**, with Caddy in front for HTTPS. The model is OpenAI's API
(`PORTER_PROVIDER=openai`, the default), or your laptop's Ollama reached through a tunnel
(`PORTER_PROVIDER=ollama`).

> **Tested status.** Everything above the "Documentation only" line is the tested path. The
> date and the result of each verification are recorded in `results/deploy_checks.json`.
> Anything below that line was written from the providers' own documentation and has not
> been run for this project.

---

## Where each piece runs

```
  customer's browser ──HTTPS──► EC2 t4g.small (Ubuntu 24.04, Docker)
                                 ┌─────────────────────────────────────────────┐
                                 │ caddy     :443  TLS for <ip>.sslip.io       │
                                 │   └─► porter :8080  (not published)         │
                                 │         ├─► online_retail.db   read-only    │
                                 │         ├─► postgres  conversations (volume)│
                                 │         └─► /state    ledger (volume)       │
                                 └──────────────────────│──────────────────────┘
                                                        │ HTTPS + key
                                                        ▼
                                  api.openai.com (gpt-4.1-mini)
               or, with ollama:   ngrok edge ──► your laptop: Ollama :11434 (gpt-oss:20b)

  observability here is JSON logs on the instance; traces, metrics and dashboards are part 2
```

| piece | where | survives a restart because |
|---|---|---|
| agent service | EC2, `porter` container | it holds nothing — every request reads its state from the two below |
| model | OpenAI's API; or with Ollama, your laptop via the ngrok tunnel | — OpenAI's is always up; the laptop's is up while the laptop is |
| business data | EC2, `online_retail.db`, mounted read-only | it is a file on the instance's disk |
| conversations | EC2, `postgres` container | the `pgdata` volume |
| ledger (returns, refunds) | EC2, `/state` | the `state` volume |
| TLS certificates | EC2, `caddy` container | the `caddy-data` volume |

## What you need

- An AWS account and a working CLI: `aws sts get-caller-identity` prints your ARN.
- An OpenAI API key for the server. Or, with `PORTER_PROVIDER=ollama`, the model tunnel running
  on the machine that has Ollama: `ngrok/README.md`.
- Docker is **not** needed on your laptop for this — the image is built on the instance.

## 1 · Create the instance

```bash
./deploy/aws/create.sh
```

It makes a key pair, a security group (SSH from your address only; 80 and 443 from
anywhere) and a `t4g.small` instance, then writes `deploy/aws/.state` with the instance id,
the public IP and the hostname `<ip-with-dashes>.sslip.io`. First boot installs Docker; the
push step waits for it.

## 2 · Configure the server's secrets

```bash
cp .env.prod.example .env.prod      # then fill it in
```

`PORTER_AUTH_SECRET` (a different one from your laptop's), `POSTGRES_PASSWORD`, and the
model: `OPENAI_API_KEY`. Every request the server answers is billed to it, so give the OpenAI
project a spending limit. With Ollama instead: `PORTER_PROVIDER=ollama`,
`PORTER_BASE_URL=https://<your-domain>.ngrok-free.app/v1` and the tunnel's token as
`PORTER_API_KEY`. The file goes to the server as `.env` and nowhere else — not into the image,
not into git.

## 3 · Gate, then ship

```bash
./release.sh 2.0.0              # builds the image, runs selfcheck + the smoke suite inside it
./deploy/aws/push.sh 2.0.0      # copies the build context up, builds there, starts it
```

`push.sh` builds the image **on the instance**. A machine with no build cache and nothing of
yours installed is the clean environment the image has to work in; if it builds and passes its
health check there, it does not depend on your laptop.

## 4 · Verify — each of these is a check in the notebook's P8

```bash
source deploy/aws/.state
curl -s https://$SITE_ADDRESS/healthz                          # external access, TLS, model reachable
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://$SITE_ADDRESS/v1/chat \
     -H 'content-type: application/json' -d '{"question":"hi"}'  # 401: authentication is on
TOKEN=$(PORTER_AUTH_SECRET=<the server's> python -m porter.auth customer 12381)
curl -s https://$SITE_ADDRESS/v1/chat -H "Authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"question":"How much was order 580638?"}'   # an answer
ssh -i ~/.ssh/porter-demo.pem ubuntu@$PUBLIC_IP 'cd porter && docker compose -f compose.prod.yaml restart porter'
# ...then continue the same thread: the conversation is still there
```

Then open the two pages: `https://$SITE_ADDRESS/` (the order desk) and `https://$SITE_ADDRESS/staff`
(the returns desk). Demo sign-in is off on the instance, so paste tokens signed with the server's
secret under "Have a token? Paste it":

```bash
PORTER_AUTH_SECRET=<the server's> python -m porter.auth customer 12381    # for the order desk
PORTER_AUTH_SECRET=<the server's> python -m porter.auth staff alice       # for the returns desk
```

## 5 · Roll back

```bash
./deploy/aws/rollback.sh 2.0.0      # no build: the old image is still on the box
curl -s https://$SITE_ADDRESS/version
```

## 6 · Tear down

```bash
./deploy/aws/destroy.sh             # instance, security group, key pair — and all data on it
```

## Things that bite, in the order they bit

- **The server has no model key.** `.env.prod` copied from the example still has
  `OPENAI_API_KEY` empty. The symptom is `/healthz` reporting `model: no API key: set
  OPENAI_API_KEY`; a key OpenAI refuses reads `model: http 401`.
- **The container cannot reach the model (Ollama).** `127.0.0.1` inside a container is the
  container. On a laptop the host's Ollama is `host.docker.internal` (compose sets
  `PORTER_OLLAMA_URL` to it); on EC2 it is the tunnel URL. The symptom is `/healthz` reporting
  `model: unreachable`.
- **Ollama refuses the tunnel with 403.** It rejects any `Host` header but its own; the tunnel
  rewrites it (`--host-header=localhost:11434`, already in the start script).
- **A health check does not restart anything under Compose.** `restart: unless-stopped` acts
  when the process exits. An `unhealthy` container that is still running stays running.
- **The public IP changes on stop/start.** So does the sslip.io hostname and its certificate.
  Re-run `create.sh` to refresh `.state`; an Elastic IP fixes it for real.
- **Let's Encrypt needs port 80 open** for the first certificate. The security group opens it.

---

## Documentation only — not run for this project

Other ways to host the same image. Each needs the same three decisions made above: where the
model is, where conversations are stored, and where the secret comes from.

| option | state that survives a restart | notes |
|---|---|---|
| **ECS on Fargate** | RDS Postgres for conversations; EFS or S3 for the ledger | no instance to patch; more pieces (ALB, task roles, a registry — ECR) |
| **AWS App Runner** | RDS Postgres; nothing on local disk survives | the simplest AWS container service; check its request timeout against `PORTER_REQUEST_TIMEOUT_S` |
| **Lightsail containers** | a Lightsail managed database | fixed monthly price; fewer knobs |
| **Fly.io / Render / Railway** | a volume, or a managed Postgres | `git push` style deploys; outside AWS |

None of these change a line of `porter/`: the service reads everything from environment
variables, keeps no state in the process, and listens on `$PORT`.
