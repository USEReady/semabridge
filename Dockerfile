# SemaBridge: one image for every deployment profile (API, worker, web app).
#   docker build -t semabridge:latest .
#   API:    docker run -p 8002:8002 -e SEMABRIDGE_DEPLOYMENT_PROFILE=azure ... semabridge:latest
#   Worker: docker run ... semabridge:latest worker

# ---- web app ---------------------------------------------------------------
FROM node:20.17-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The UI calls the API on the same origin (/api, /auth).
RUN npm run build

# ---- python dependencies -----------------------------------------------------
FROM python:3.11.10-slim-bookworm AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
COPY src/ ./src/
RUN /opt/venv/bin/pip install .

# ---- runtime -----------------------------------------------------------------
FROM python:3.11.10-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SEMABRIDGE_DEPLOYMENT_PROFILE=azure \
    SEMABRIDGE_FRONTEND_DIST=/app/web \
    SEMABRIDGE_DATA_DIR=/data \
    API_PORT=8002
RUN groupadd --system semabridge && useradd --system --gid semabridge --home-dir /data/home semabridge \
    && mkdir -p /data && chown semabridge:semabridge /data
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY --from=web /web/dist /app/web
COPY Config/ ./Config/
COPY deploy/docker/entrypoint.sh /usr/local/bin/semabridge-entrypoint
RUN chmod 0755 /usr/local/bin/semabridge-entrypoint && chown -R semabridge:semabridge /app
USER semabridge
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/api/health/live', timeout=4).status == 200 else 1)"
ENTRYPOINT ["semabridge-entrypoint"]
CMD ["api"]
