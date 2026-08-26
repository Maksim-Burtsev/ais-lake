FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-group dev --no-install-project

COPY ais_pipeline ./ais_pipeline
RUN uv sync --frozen --no-group dev

CMD ["uv", "run", "--no-sync", "python", "-m", "ais_pipeline.ingestor"]
