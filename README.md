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

## Configuration (optional environment variables)

| Variable | Purpose |
|---|---|
| `YT_COOKIES_FILE` | Path to a Netscape-format `cookies.txt`, used when YouTube asks you to "Sign in to confirm you're not a bot". |
| `YT_COOKIES_FROM_BROWSER` | Read cookies straight from a browser, e.g. `chrome`, `firefox`, `edge:Profile 1`. |
| `YT_PROXY` | Proxy URL passed to yt-dlp. |

## Notes

- **Bot checks:** from cloud or datacenter IPs, YouTube often blocks its default player clients. When that happens, the server retries with other clients (`web_embedded`, `tv_simply`, `mweb`) until it gets caption tracks. If that still fails, set cookies.
- **Translated captions:** captions that YouTube machine-translates (e.g. `ta` for an English video) are heavily rate-limited (HTTP 429). Captions in the video's original language are reliable.
- **Keep yt-dlp current:** YouTube changes often, so update yt-dlp regularly with `uv lock --upgrade-package yt-dlp`.
