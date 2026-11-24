# syntax=docker/dockerfile:1
# One slim image: built React app + FastAPI API on port 8080.
# The Claude Code CLI is NOT bundled: it is downloaded (pinned, checksum-verified) into the data
# volume the first time a user connects their Claude subscription.

# ---- 1. React frontend -------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python dependencies, trimmed -----------------------------------------
FROM python:3.12-slim AS pydeps
# Slow or flaky links: pip's default 15 s read timeout fails whole builds on a single stalled download.
ENV PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=8
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --root-user-action=ignore --prefix=/install -r /tmp/requirements.lock \
 && SP=/install/lib/python3.12/site-packages \
 # test suites shipped inside wheels are never imported at runtime
 && rm -rf "$SP/pandas/tests" \
 && find "$SP/numpy" -type d -name tests -prune -exec rm -rf {} + \
 # debug symbols in compiled extensions
 && find /install -name "*.so*" -type f -exec strip --strip-unneeded {} + \
 && python -m compileall -q -j 0 "$SP"

# ---- 3. Runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime
ARG CLAUDE_CODE_VERSION=2.1.310
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIA_DATA_DIR=/data \
    LIA_STATIC_DIR=/app/frontend/dist \
    CLAUDE_CONFIG_DIR=/data/claude \
    CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION} \
    HOME=/data/home \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
# pip is only needed to build; drop it from the runtime image
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/lib/python3.12/ensurepip /usr/local/bin/pip* \
 && useradd --uid 10001 --no-create-home --home-dir /data/home app \
 && mkdir -p /data/home /data/claude \
 && chown -R app:app /data
COPY --from=pydeps /install /usr/local
WORKDIR /app
COPY analyzer/ analyzer/
COPY server/ server/
COPY --from=frontend /src/frontend/dist frontend/dist
# MIT requires the licence notice in every copy, and the image is one; the notice travels with it too
COPY LICENSE DISCLAIMER.md ./
RUN python -m compileall -q analyzer server
USER app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"]
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
