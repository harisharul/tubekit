FROM ghcr.io/astral-sh/uv:0.11-python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app

ENV MCP_TRANSPORT=http HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/youtube-transcript-mcp"]
