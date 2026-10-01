# Pending on you — part 2, v2

What only you can do, in order. A working list: delete it once it's done.

Already done without you (2026-10-01): the notebook ran top to bottom through P3 on this Mac, in an
isolated copy of this folder, with Docker Desktop: the image, the container with Postgres beside it,
a conversation carried on by its `thread_id`, a return approved twice and paid once, Prometheus,
Grafana and a minute of traffic. Its outputs are saved. The chat page and the returns desk were
clicked through against the running containers. P4 (AWS) has not run.

- [x] **`.env` in this folder:** written 2026-10-01 from your key file (the key only, never printed).
- [x] **Ports 3000 and 9090 free:** your Langfuse stack held them; you removed it (2026-10-01).
- [ ] **AWS credentials, for P4:** `aws configure`, then `aws sts get-caller-identity`. Checked
  2026-10-01 18:05: "The security token included in the request is invalid".
- [ ] **A dry run of P4 before the session, if there's time:** `create.sh` and `push.sh`. The first
  push waits for Docker to install on the instance and builds there: a few minutes.
- [ ] **Afterwards:** `./deploy/destroy.sh`, so the instance stops costing money.
