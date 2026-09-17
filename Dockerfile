FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /bin/uv

# Non-root user with UID 1000 (required by Hugging Face Spaces, harmless elsewhere).
RUN useradd --create-home --uid 1000 app
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY scripts/start.sh ./scripts/start.sh
RUN uv sync --frozen --no-dev --extra postgres && chown -R app:app /app

USER app
# Hugging Face Spaces routes to 7860; other hosts set PORT.
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "scripts/start.sh"]
