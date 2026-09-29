# Pending on you — part 1

What only you can do here, because it starts a server, spends money or involves your secrets,
in order. After each step, what I do with it. A working list: delete it once it's done.

## Once — this also unblocks part 2

- [ ] **Switch this folder's `.env` to OpenAI** (the notebook's default model now).
  - Delete the three Ollama lines: `PORTER_MODEL=…`, `PORTER_BASE_URL=…`, `PORTER_API_KEY=…`.
    While they are there, they override the provider and everything stays on Ollama.
  - Add `OPENAI_API_KEY=<your key>`. `PORTER_PROVIDER` can stay out: `openai` is the default.
  - Or tell me, and I'll make both edits, copying the key from `GEN_AI_REF/openai_key.env`
    without displaying it.
  - Check: `python check_setup.py --quick` shows `model endpoint ✅ gpt-4.1-mini at https://api.openai.com/v1`.
  - Part 2's `.env` stays as it is: part 2 runs on Ollama until its own switch.
- [ ] **Start Docker Desktop** and wait for "Engine running".
  - Check: `docker info` prints a server version.
- [ ] **Make a new AWS access key.** The configured one is rejected (`InvalidClientTokenId`:
  that key no longer exists).
  - IAM console → Users → your user → Security credentials → **Create access key** → Command Line Interface.
  - `aws configure`, paste the key id and the secret. The region you give there doesn't matter;
    the deploy scripts choose their own.
  - Check: `aws sts get-caller-identity` prints your ARN.
- [ ] **Claim the free ngrok domain** — no longer needed for part 1, whose deployment uses
  OpenAI; part 2's release exercise still deploys on Ollama through the tunnel.
  dashboard.ngrok.com → **Domains** → claim the static domain.
  - Put it in both `.env` files as `NGROK_DOMAIN=<name>.ngrok-free.app`.
  - Nothing else to set: the tunnel's token (`PORTER_MODEL_TOKEN`) is already in `.env`, and
    ngrok's authtoken is already on this machine.

**Then tell me.** I build the image, run P7's container checks and `./release.sh 2.0.0`
(all one-shot: `docker build` and `docker run --rm`).

## P7 · The container

- [ ] In a terminal here: `docker compose up --build`, and leave it running.
  - **Then tell me:** I record the health check, a first answer and `docker compose ps`
    → `results/container_checks.json`.
- [ ] `docker compose restart porter`
  - **Then tell me:** I continue the same conversation, which only works if Postgres kept it.

## P8 · Off the laptop

- [ ] *(Only to record the Ollama route too)* **Start the tunnel:** `./ngrok/start_model_tunnel.sh`.
  - **Then tell me:** I record the three edge checks (token → 200, no token → 401, Ollama's
    own API → 404) → `results/tunnel_checks.json`. Without it, P8's tunnel cell says the tunnel
    is only for Ollama and moves on.
- [ ] **Create the instance:** `./deploy/aws/create.sh`
  - The region defaults to us-west-2; `AWS_REGION=ap-south-1 ./deploy/aws/create.sh` puts it in Mumbai.
  - About two cents an hour, plus the public IP and the disk, until `destroy.sh`.
  - It prints the instance id, the future site URL and the ssh command, and writes `deploy/aws/.state`.
- [ ] **Write `.env.prod`:** `cp .env.prod.example .env.prod`, then fill in
  - `PORTER_AUTH_SECRET`: a new one, not the laptop's —
    `python -c "import secrets; print(secrets.token_urlsafe(48))"`
  - `POSTGRES_PASSWORD`: anything long
  - `OPENAI_API_KEY`: the key the server bills to. Give the OpenAI project a spending limit first.
  - Or tell me, and I'll fill it in.
  - The P8 incident is this file with `OPENAI_API_KEY` still empty: push once without it, and
    the health check says `model: no API key: set OPENAI_API_KEY`.
- [ ] **Deploy:** `./deploy/aws/push.sh 2.0.0`
  - The first time, it waits for the instance to finish installing Docker, then builds on the instance.
  - Check: the URL it prints shows `2.0.0`.
  - **Then tell me:** I record the health check, the 401 without a token, and an answer
    → `results/deploy_checks.json`.
- [ ] **Restart the service on the instance:**
  `source deploy/aws/.state && ssh -i ~/.ssh/porter-demo.pem ubuntu@$PUBLIC_IP 'cd porter && docker compose -f compose.prod.yaml restart porter'`
  - **Then tell me:** I continue the conversation; it has to come back 200, not 404.

## When part 1 is recorded

- **Keep the instance.** Part 2 ships 2.1.0 to it and rolls back to 2.0.0; `./deploy/aws/destroy.sh`
  comes after that.
- `docker compose down` here before starting part 2's stack: both use port 8080. Conversations
  stay in the volume.
- The tunnel can stop until part 2's release exercise.

## If something goes wrong

- **ssh or `push.sh` hangs:** the security group lets port 22 in only from the IP `create.sh`
  saw. After a network change, allow the new one:
  `source deploy/aws/.state && aws ec2 authorize-security-group-ingress --region $REGION --group-id $SECURITY_GROUP --protocol tcp --port 22 --cidr $(curl -s https://checkip.amazonaws.com)/32`
- **Stopped and started the instance?** Its public IP changed. Re-run `./deploy/aws/create.sh`
  (it creates nothing new and refreshes `.state`), then push again.
- **Port 8080 busy:** another stack is running, part 2's or a `uvicorn`.

## Not on you

Building the image, the container checks, `./release.sh 2.0.0`, recording every result, and the
final top-to-bottom run of the notebook (keep Docker Desktop running for it).
