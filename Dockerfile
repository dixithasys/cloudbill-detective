FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY cloudbill_detective ./cloudbill_detective
COPY tests ./tests
COPY evals ./evals
COPY iam ./iam

RUN pip install --upgrade pip && pip install .

# Create non-root user and cache directory
RUN useradd --create-home appuser && \
    mkdir -p /app/.cache && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Default entrypoint runs the MCP server; override args with ["--transport", "http"] for HTTP mode
ENTRYPOINT ["python", "-m", "cloudbill_detective.server"]
CMD ["--transport", "stdio"]

