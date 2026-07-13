# syntax=docker/dockerfile:1
FROM python:3.11-slim

# git is needed to install csda-client from GitHub
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

# uv: fast dependency install
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for better layer caching
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "csdap_agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
