# youtube-transcript-mcp

An MCP server, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), with two read-only tools:

| Tool | What it does |
|---|---|
| `list_playlist_videos(playlist, limit?)` | Lists the videos in a playlist: position, ID, title, URL, duration, channel. Takes a playlist URL or ID. |
| `get_video_transcript(video, language="en", prefer_manual=true, include_timestamps=false)` | Gets a video's captions as plain text, with optional `[mm:ss]` timestamps on each line. Takes a video URL or 11-character ID. |

Nothing is downloaded except metadata and caption files. The server uses creator-uploaded captions when they exist and auto-generated ones otherwise. Regional variants count as a match, so asking for `en` can return `en-US`. If the requested language doesn't exist, the error lists the languages that do.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Run

```bash
uv sync
```

```bash
uv run youtube-transcript-mcp
```

The server talks over stdio.

### Claude Code

```bash
claude mcp add youtube-transcript -- uv run --directory /path/to/youtube-transcript youtube-transcript-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "youtube-transcript": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/youtube-transcript", "youtube-transcript-mcp"]
    }
  }
}
```

## Use from Claude chat (web / mobile): deploy as a remote server

Claude chat can only reach MCP servers on a public HTTPS URL, which you add as a **custom connector**. In HTTP mode the server speaks Streamable HTTP:

```bash
MCP_TRANSPORT=http PORT=8000 MCP_SECRET_PATH=change-me uv run youtube-transcript-mcp
```

The endpoint is `http://<host>:8000/mcp/change-me`.

### Option 0: Windows + WSL + Docker with your own domain (recommended for home use)

See [docs/wsl-docker-setup.md](docs/wsl-docker-setup.md). It uses `docker compose`, a named Cloudflare tunnel, a permanent URL, and auto-start at login.

### Option A: Google Cloud Run (Mumbai region)

```bash
gcloud run deploy youtube-transcript-mcp --source . --region asia-south1 --allow-unauthenticated --memory 512Mi --max-instances 2 --set-env-vars MCP_SECRET_PATH=$(openssl rand -hex 24)
```

The connector URL is `<service URL>/mcp/<secret>`. To read the secret back later:

```bash
gcloud run services describe youtube-transcript-mcp --region asia-south1 --format='value(spec.template.spec.containers[0].env)'
```

### Option B: your own machine + Cloudflare Tunnel

This keeps YouTube traffic on your home connection, which avoids most bot checks.

```bash
MCP_TRANSPORT=http PORT=8000 MCP_SECRET_PATH=change-me uv run youtube-transcript-mcp
```

```bash
cloudflared tunnel --url http://localhost:8000
```

`cloudflared` prints a `https://<random>.trycloudflare.com` URL. Add `/mcp/change-me` to the end of it. That quick-tunnel URL changes every time you restart; use a named tunnel for a stable one.

### Add it in Claude

Go to **Settings → Connectors → Add custom connector**, give it a name, and paste the full URL including the secret path. Leave the OAuth fields empty. Then enable the connector in a chat from the tools menu. Connectors added on claude.ai also work in the mobile apps.

### Security notes

- `MCP_SECRET_PATH` is a shared secret in the URL, not real authentication. Treat the URL like a password and rotate it by redeploying with a new value. For multi-user use, put the server behind an OAuth-capable gateway.
- The tools are read-only and only fetch public YouTube data. The main risk from a leaked URL is someone else using your compute and your IP's YouTube reputation.
- Don't bake `cookies.txt` into the image. If you need cookies on a host, mount them as a secret and point `YT_COOKIES_FILE` at the mounted file.

## Configuration (optional environment variables)

| Variable | Purpose |
|---|---|
| `YT_COOKIES_FILE` | Path to a Netscape-format `cookies.txt`, used when YouTube asks you to "Sign in to confirm you're not a bot". |
| `YT_COOKIES_FROM_BROWSER` | Read cookies straight from a browser, e.g. `chrome`, `firefox`, `edge:Profile 1`. |
| `YT_PROXY` | Proxy URL passed to yt-dlp. |
| `MCP_TRANSPORT` | `stdio` (default) or `http`. |
| `HOST` / `PORT` | HTTP bind address (default `0.0.0.0:8000`). |
| `MCP_SECRET_PATH` | Serve the HTTP endpoint at `/mcp/<secret>` instead of `/mcp`. |

## Notes

- **Bot checks:** from cloud or datacenter IPs, YouTube often blocks its default player clients. When that happens, the server retries with other clients (`web_embedded`, `tv_simply`, `mweb`) until it gets caption tracks. If that still fails, set cookies.
- **Translated captions:** captions that YouTube machine-translates (e.g. `ta` for an English video) are heavily rate-limited (HTTP 429). Captions in the video's original language are reliable.
- **Keep yt-dlp current:** YouTube changes often, so update yt-dlp regularly with `uv lock --upgrade-package yt-dlp`.
