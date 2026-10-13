# Build context: the repository root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1     UV_LINK_MODE=copy     UV_PYTHON_DOWNLOADS=never     PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so source edits don't invalidate this layer.
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
COPY packages/api/pyproject.toml packages/api/
COPY packages/worker/pyproject.toml packages/worker/
COPY packages/sweeper/pyproject.toml packages/sweeper/
RUN uv sync --frozen --no-dev --no-install-workspace --package worker

COPY packages/common packages/common
COPY packages/worker packages/worker
RUN uv sync --frozen --no-dev --no-editable --package worker

RUN useradd --create-home --uid 1000 app
USER app
CMD ["celery", "--app", "worker.app", "worker", "--loglevel", "INFO", "--concurrency", "1"]
