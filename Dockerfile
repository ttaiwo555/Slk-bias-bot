# SLK Bias Bot Dockerfile
FROM python:3.11-slim

# Prevent Python from writing .pyc and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and requirements
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

# Copy application code and config
COPY slk_bot/ slk_bot/
COPY analysis/ analysis/

# Ensure storage directory exists
RUN mkdir -p slk_bot/storage

# Default entrypoint: launch bot scheduler and Telegram listener
CMD ["python", "-m", "slk_bot.bot.main"]
