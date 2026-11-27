# Analysis Tracker — container image
#
#   docker build -t analysis-tracker .                  # runtime image
#   docker build --target test -t analysis-tracker:test .   # runs the test suite during the build
#
# The app keeps everything (SQLite database + content-addressed blobs) under /data.
# Mount a volume or host directory there. Git repositories for commit checks go read-only under /repos.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---- deps: build the virtualenv once --------------------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---- test: same deps + pytest; the build fails if a test fails -----------------------------
FROM deps AS test
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements-dev.txt
WORKDIR /app
COPY . .
RUN /opt/venv/bin/python -m pytest -q -p no:cacheprovider

# ---- runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
# git is only used to verify commit SHAs against repositories mounted at /repos.
# safe.directory: mounted repos belong to another uid, which git would otherwise refuse to read.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*' \
    && useradd --system --uid 10001 --user-group --home-dir /app --no-create-home tracker \
    && mkdir -p /data /repos && chown tracker:tracker /data

COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY tracker ./tracker
COPY run.py main.py demo.py ./

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DATA_DIR=/data \
    REPOS_DIR=/repos \
    MAX_UPLOAD_MB=500

# Runs as a non-root user. Compose overrides the uid with the host user's so bind-mounted data stays yours.
USER tracker
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=4)" || exit 1

# Same start command as outside Docker (see main.py), without --reload. Compose maps the host port to 8000.
# uvicorn runs as PID 1 without an init: it handles SIGTERM itself, and the only child processes
# (git checks) are waited for, so nothing needs reaping. For an init anyway, set `init: true` in compose.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
