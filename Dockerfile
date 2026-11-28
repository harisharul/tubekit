# Pinned by digest; Dependabot proposes updates.
FROM ghcr.io/astral-sh/uv:0.13.10-python3.13-trixie-slim@sha256:00e2b362982a5ce19eb3a51453ed76af92ccaaedccc0236fae83b27d994ae0f3

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER 10001

ENV MCP_TRANSPORT=http HOST=0.0.0.0 PORT=8000 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" DENO_DIR=/tmp/deno DENO_NO_UPDATE_CHECK=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ['PORT'])), 2)"]
CMD ["/app/.venv/bin/tubekit-mcp"]
