# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./
COPY parse_apis/pyproject.toml parse_apis/pyproject.toml
COPY parse_apis/src/ parse_apis/src/

# Install dependencies (frozen from lockfile, no dev deps)
RUN uv sync --frozen --no-dev

# Copy the rest of the application
COPY .streamlit/ .streamlit/
COPY src/ src/
COPY app.py .

# Streamlit configuration
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true

EXPOSE 8080

CMD ["uv", "run", "streamlit", "run", "app.py"]
