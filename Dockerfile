# Kernso MCP Server
# Multi-stage build, runs as non-root user
# Works for both: docker run -i (stdio) and HTTP (Cloud Run)

FROM python:3.12-slim AS base

RUN groupadd -r kernso && useradd -r -g kernso -m kernso

WORKDIR /app

# Install deps (with private PyPI for shared packages)
COPY requirements.txt .
ARG ARTIFACT_REGISTRY_TOKEN
RUN --mount=type=secret,id=pypi_token \
    pip install --no-cache-dir \
    --extra-index-url "https://oauth2accesstoken:$(cat /run/secrets/pypi_token 2>/dev/null || echo ${ARTIFACT_REGISTRY_TOKEN})@us-east1-python.pkg.dev/kernso-reddit-data-1/kernso-python/simple/" \
    -r requirements.txt

# Copy application
COPY src/ src/

# Switch to non-root
USER kernso

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Default: HTTP mode for Cloud Run
CMD ["python", "-m", "kernso_mcp.server", "--port", "8080"]
