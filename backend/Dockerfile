# The API image: FastAPI + DBOS pipeline, the Playwright renderer and the `claude` CLI for agent stages.
# Build from the REPO ROOT (the API reads agent/, policies/ and demo/ next to backend/):
#   docker build -f backend/Dockerfile -t nemo-k-cards-api .
# No configuration or secret is baked in: everything comes from the env file at run time (deploy/env.example).
# ANTHROPIC_API_KEY authenticates `claude` at run time; there is never a personal login in the image.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# --- claude: Claude Code ships as a native binary; npm is only the installer (same as infra/sandbox/Dockerfile).
FROM node:22-bookworm-slim AS claude
ARG CLAUDE_CODE_VERSION=2.1.292
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && install -m 0755 "$(npm root -g)/@anthropic-ai/claude-code/bin/claude.exe" /claude \
    && /claude --version

# --- deps: the locked Python environment (no dev group), built apart so code changes don't reinstall it.
FROM ${PYTHON_IMAGE} AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/backend/.venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# --- runtime
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/backend/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/app \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    OUTPUT_DIR=/data/out

COPY --from=deps /app/backend/.venv /app/backend/.venv
# Chromium's headless shell only (the renderer runs headless) plus its system libraries and fallback fonts
# (Noto emoji, CJK, Liberation). The card fonts themselves (Pretendard etc.) ship in backend/app/renderer/fonts.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
COPY --from=claude /claude /usr/local/bin/claude

WORKDIR /app
# Only what the API reads at run time: its code, the agent workspace, policies and demo fixtures.
# Owned by root and read-only for `app`; the API writes only to /data (a volume) and $HOME (claude's state).
COPY backend/pyproject.toml /app/backend/
COPY backend/app /app/backend/app
COPY agent /app/agent
COPY policies /app/policies
COPY demo/scenarios /app/demo/scenarios
# Plain SQL, read by `python -m app.migration_check` (wok runs it before a deploy restarts anything).
COPY supabase/migrations /app/supabase/migrations

USER app
WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# One process: DBOS runs the pipeline inside it, and resumes interrupted Jobs on start.
# --proxy-headers: the API sits behind the compose front (Caddy) and is not published to the internet itself.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
