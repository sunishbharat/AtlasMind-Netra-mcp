# Multi-platform build: supports linux/amd64 and linux/arm64.
# All three base images publish multi-platform manifests, so buildx resolves
# the correct variant per target architecture automatically.
# Build command: see scripts/cf-deploy.sh or .github/workflows/docker.yml

# -- Stage 1: extract Valkey binaries -----------------------------------------
FROM valkey/valkey:8-bookworm AS valkey

# -- Stage 2: install Python dependencies with uv -----------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

# -- Stage 3: final runtime image ---------------------------------------------
FROM python:3.12-slim AS final

WORKDIR /app

# Valkey binaries from stage 1
COPY --from=valkey /usr/local/bin/valkey-server /usr/local/bin/valkey-server
COPY --from=valkey /usr/local/bin/valkey-cli    /usr/local/bin/valkey-cli

# Python virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Entrypoint (flat copy into /app so ENTRYPOINT path matches)
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Application source (data/ is excluded by .dockerignore; dirs created below)
COPY server.py    ./
COPY config/      ./config/
COPY core/        ./core/
COPY briefings/   ./briefings/
COPY memory/      ./memory/
COPY models/      ./models/
COPY prompts/     ./prompts/

# Writable data directories - ephemeral in CF, mountable in docker-compose
RUN mkdir -p /app/data/logs /app/data/reports

# Baked-in defaults (all overridable via env vars; PORT/SESSION/VALKEY set in entrypoint.sh)
ENV NETRA_SERVER__TRANSPORT=streamable-http \
    NETRA_SERVER__HOST=0.0.0.0 \
    NETRA_LOG__JSON_LOGS=true \
    NETRA_LOG__LOG_FILE="" \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD /app/.venv/bin/python -c "import urllib.request, os; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('NETRA_SERVER__PORT', os.getenv('PORT', '8765')) + '/health')"

ENTRYPOINT ["/app/entrypoint.sh"]
