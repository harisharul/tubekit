# Security

## Reporting a vulnerability

Please report vulnerabilities privately through **GitHub → Security → Report a vulnerability** on this repository. Don't open a public issue. You can expect an acknowledgement within a few days.

## Threat model

TubeKit exposes read-only tools that fetch public YouTube data. Its inputs come from an LLM, which may be steered by its users or by untrusted content. The design assumes every tool argument is hostile.

| Risk | Mitigation |
|---|---|
| **SSRF:** making the server fetch internal or cloud metadata URLs | Every video or playlist input is reduced to a YouTube ID and rebuilt as a canonical `https://www.youtube.com/...` URL. Anything else is rejected before any network call. yt-dlp is restricted to its YouTube extractors, and caption URLs must be HTTPS on a YouTube host. |
| **Resource exhaustion** | String inputs are length-capped and request bodies are limited to 64 KB. Limits also cap playlist positions (5,000), title-filter scans (2,000 videos), caption size (5 MB), concurrent YouTube fetches, concurrent HTTP connections and cache size. Network calls time out. |
| **Host header attacks and DNS rebinding** | `MCP_ALLOWED_HOSTS` rejects requests for other hostnames (HTTP 421). On `127.0.0.1` the server accepts only localhost. Requests with an untrusted browser `Origin` are rejected. |
| **Secret leakage** | Access logs are disabled, so `MCP_SECRET_PATH` never appears in logs. No credentials are needed or stored. Error messages contain no internal details. |
| **Prompt injection** via video titles or captions | The tools are read-only. Server instructions tell the client to treat returned content as data, not instructions. Clients should still review tool use, especially alongside tools that can take actions. |
| **Container escape or tampering** | The container runs as a non-root user with a read-only root filesystem, no Linux capabilities and `no-new-privileges`. The base image is pinned by digest and dependencies are locked by hash (`uv.lock`). Dependabot proposes updates. |

## Deploying publicly

- **Put the server behind a TLS-terminating proxy or tunnel**, set `MCP_ALLOWED_HOSTS`, and don't expose the container port directly.
- **Rate-limit at the edge** (e.g. CDN or WAF rules). The server's own limits protect the server, not your outbound IP's reputation with YouTube.
- **Don't set `YT_COOKIES_FILE` on a shared deployment.** Every user would act as your YouTube account.
- **The endpoint has no user authentication.** `MCP_SECRET_PATH` only obscures the URL. For restricted access, use an OAuth-capable gateway in front of the server.
