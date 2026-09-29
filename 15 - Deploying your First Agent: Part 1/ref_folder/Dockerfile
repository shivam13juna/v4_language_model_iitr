# syntax=docker/dockerfile:1
#
# Two stages. The first has pip and its caches; the second has only the installed packages
# and the source. Nothing needed to *build* the image survives into the one you ship.
#
#   docker build --build-arg PORTER_VERSION=2.0.0 -t porter:2.0.0 .
#   docker run --rm porter:2.0.0 python -m porter.selfcheck      # a clean-environment check
#
# What is deliberately NOT in here: the orders database (mounted read-only at /data), any
# secret (passed as environment at run time), and anything the service writes (a volume at
# /state). An image with any of those inside it is an image you rebuild to rotate a key.

# --- stage 1: the dependencies -----------------------------------------------------------
FROM python:3.12-slim-bookworm AS build

WORKDIR /app
# Dependencies change far less often than source, so they get their own layer, above the
# source. Reverse these two COPYs and every edit to agent.py reinstalls LangChain.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt

# --- stage 2: the image that ships ----------------------------------------------------------
FROM python:3.12-slim-bookworm

# A container that runs as root runs your agent as root. There is no upside.
RUN useradd --create-home --uid 10001 porter
WORKDIR /app

COPY --from=build /install /usr/local
COPY porter/ ./porter/

# The version this image answers as — on every response header, on /version and on the
# porter_build_info metric. The one question after every deploy and every rollback.
ARG PORTER_VERSION=dev
LABEL org.opencontainers.image.title="porter" \
      org.opencontainers.image.version="${PORTER_VERSION}"

# $PORT because every hosting platform assigns one and expects you to listen on it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PORTER_VERSION=${PORTER_VERSION} \
    PORTER_DB_PATH=/data/online_retail.db \
    PORTER_CHECKPOINT_PATH=/state/porter_threads.sqlite \
    PORTER_LEDGER_PATH=/state/porter_ledger.sqlite \
    PORTER_JSON_LOGS=true \
    PORTER_METRICS=true

RUN mkdir -p /data /state && chown porter:porter /state
USER porter
EXPOSE 8080

# Docker records this as healthy/unhealthy. It does NOT restart an unhealthy container —
# neither does Compose; `restart:` acts only when the process exits. What uses it: another
# service's `depends_on: condition: service_healthy`, `docker ps`, and orchestrators that do
# act on health (ECS, Kubernetes via their own probes). The check touches the database, the
# model endpoint and the ledger volume, because a check that touches nothing says nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys,json; \
r=json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz')); \
sys.exit(0 if r['status']=='ok' else 1)"

CMD ["sh", "-c", "exec uvicorn porter.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
