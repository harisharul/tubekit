# TubeKit

An [MCP](https://modelcontextprotocol.io) server with YouTube tools for Claude and other MCP clients, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp). There's no YouTube API key to manage, and nothing is downloaded except metadata and captions.

## Tools

| Tool | What it does |
|---|---|
| `list_playlist_videos(playlist, start=1, end?, title_filter?)` | Lists playlist videos as `position. title [id] duration`, 50 per call by default and 200 at most. Only the requested range is fetched. `title_filter` returns only titles that contain all the given words. |
| `get_video_transcript(video, language="en", include_timestamps=false, start_time?, end_time?, offset=0, max_chars=20000, prefer_manual=true)` | Returns a transcript one page at a time, optionally limited to a time window. The first page lists the video's chapters. |
| `search_transcript(video, keywords, language="en", max_results=10, snippets=true)` | Finds *when* topics come up. The client passes the user's term plus synonyms (e.g. `["loss function", "cost function", "cross-entropy"]`). Returns time ranges with hit counts and short quotes, ready to pass to `get_video_transcript`. |

The tools are designed to save tokens:

- **Compact text output**, not JSON.
- **Paging by default.** Results end with a `More: …` hint when there is more.
- **Server-side filtering**, so the model never receives a whole playlist or transcript just to find one thing.
- **An in-memory transcript cache**, so paging through a long video hits YouTube only once.

## Run locally

Requires [uv](https://docs.astral.sh/uv/). It installs Python 3.13 for you if needed.

```bash
git clone https://github.com/harisharul/tubekit.git
```

```bash
cd tubekit && uv sync
```

### Claude Desktop or Claude Code (stdio)

The client starts the server itself; nothing listens on the network.

```bash
claude mcp add tubekit -- uv run --directory /path/to/tubekit tubekit-mcp
```

For Claude Desktop, add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tubekit": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/tubekit", "tubekit-mcp"]
    }
  }
}
```

### As an HTTP server (remote MCP / custom connectors)

```bash
MCP_TRANSPORT=http HOST=127.0.0.1 PORT=8000 uv run tubekit-mcp
```

The endpoint is `http://127.0.0.1:8000/mcp`.

## Run with Docker

```bash
cp .env.example .env
```

```bash
docker compose up -d --build
```

The endpoint is `http://127.0.0.1:8000/mcp`. The container runs as a non-root user with a read-only filesystem, all Linux capabilities dropped, and memory and process limits. To reach it from the internet, put it behind a reverse proxy or tunnel that provides TLS, and set `MCP_ALLOWED_HOSTS`. `docker-compose.yml` includes an optional Cloudflare tunnel service (see `.env.example`).

To use the image without Compose:

```bash
docker build -t tubekit-mcp .
```

```bash
docker run --rm -p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp --cap-drop ALL tubekit-mcp
```

## Configuration

All settings are environment variables, and all are optional.

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` (`http` in Docker) | `stdio` or `http`. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP bind address. Use `127.0.0.1` when running directly on your machine. |
| `MCP_ALLOWED_HOSTS` | *(none)* | Comma-separated `Host` headers to accept, e.g. `tubekit.example.com`. **Set this in any deployment behind a proxy.** Other hosts get HTTP 421. |
| `MCP_SECRET_PATH` | *(none)* | Serve at `/mcp/<secret>` instead of `/mcp`. It's a shared secret, not real authentication. |
| `MCP_MAX_CONNECTIONS` | `100` | Concurrent HTTP connections before the server returns 503. |
| `TUBEKIT_MAX_CONCURRENCY` | `4` | Concurrent YouTube fetches. Extra requests wait up to 30 seconds, then fail. |
| `YT_PROXY` | *(none)* | Outbound proxy for yt-dlp. |
| `YT_COOKIES_FILE` | *(none)* | `cookies.txt` for YouTube sign-in checks. **Personal use only:** never use it on a shared server. |

## Security

See [SECURITY.md](SECURITY.md) for the threat model and for how to report a vulnerability.

## Development

```bash
uv run pytest
```

The tests run offline. Keep yt-dlp current, because YouTube changes often:

```bash
uv lock --upgrade-package yt-dlp
```
