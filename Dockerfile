FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY papyrus/ ./papyrus/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN groupadd --gid 10001 papyrus && useradd --uid 10001 --gid papyrus --no-create-home papyrus \
    && mkdir -p /var/lib/papyrus/media && chown papyrus:papyrus /var/lib/papyrus/media
COPY --from=builder /app/.venv /app/.venv
COPY alembic/ ./alembic/
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" \
    MEDIA_STORAGE_ROOT="/var/lib/papyrus/media" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER papyrus
EXPOSE 8080
CMD ["papyrus-server"]
