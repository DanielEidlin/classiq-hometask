# Build context: the repository root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1     UV_LINK_MODE=copy     UV_PYTHON_DOWNLOADS=never     PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Dependencies first, so source edits don't invalidate this layer. Only the workspace
# members this image installs are copied; uv installs from uv.lock without the others.
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/
RUN uv sync --frozen --no-dev --no-install-workspace --package common --extra migrate

COPY packages/common packages/common
RUN uv sync --frozen --no-dev --no-editable --package common --extra migrate

RUN useradd --create-home --uid 1000 app
USER app
CMD ["alembic", "-c", "packages/common/alembic.ini", "upgrade", "head"]
