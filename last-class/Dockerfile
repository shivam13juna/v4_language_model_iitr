# syntax=docker/dockerfile:1
#
# Two stages. The first has pip and its caches; the second has only the installed packages
# and the source. Nothing needed to *build* the image survives into the one you ship.
#
#   docker build -t porter:1.0 .
#
# What is deliberately NOT in here: the orders database (mounted read-only at /data), any
# secret (passed as environment at run time), and anything the service writes (a volume at
# /state). An image with any of those inside it is an image you rebuild to rotate a key.

# --- stage 1: the dependencies -----------------------------------------------------------
FROM python:3.12-slim AS build

WORKDIR /app
# Dependencies change far less often than source, so they get their own layer, above the
# source. Reverse these two COPYs and every edit to agent.py reinstalls LangChain.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt

# --- stage 2: the image that ships ----------------------------------------------------------
FROM python:3.12-slim

# A container that runs as root runs your agent as root. There is no upside.
RUN useradd --create-home --uid 10001 porter
WORKDIR /app

COPY --from=build /install /usr/local
COPY porter/ ./porter/

# Where the orders are mounted, and where the ledger is written: /state, the one folder the
# porter user may write to.
ENV PYTHONUNBUFFERED=1 \
    PORTER_DB_PATH=/data/online_retail.db \
    PORTER_LEDGER_PATH=/state/porter_ledger.sqlite

RUN mkdir -p /data /state && chown porter:porter /state
USER porter
EXPOSE 8080

# Docker records this as healthy/unhealthy. It does NOT restart an unhealthy container, and
# neither does Compose: `restart:` acts only when the process exits. It reads /healthz, which
# touches the orders, because a check that touches nothing says nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

# 0.0.0.0, not 127.0.0.1: inside a container, 127.0.0.1 is the container itself, and nothing
# outside it could connect.
CMD ["uvicorn", "porter.api:app", "--host", "0.0.0.0", "--port", "8080"]
