# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies
# build-essential: for compiling C extensions (tree-sitter, etc.)
# libpq-dev: for psycopg (Postgres driver)
# git: for git-based dependencies if any
# curl: for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

# Install the package with all dependencies
# We install with [all] extra to include UI, postgres, cache, etc.
RUN pip install --upgrade pip && \
    pip install ".[all]"

# Create a non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port for Streamlit UI
EXPOSE 8501

# Default command (can be overridden in docker-compose)
# Runs the CLI help by default
CMD ["integration-coworker", "--help"]
