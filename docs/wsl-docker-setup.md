# Run on Windows (WSL + Docker) with a permanent Cloudflare URL

This guide runs two containers inside WSL:

- `mcp` is the MCP server. It isn't published on any host port, so nothing on your PC or your LAN can reach it directly.
- `tunnel` is a Cloudflare named tunnel. It makes an outbound-only connection to Cloudflare and serves the server at `https://<sub>.<your-domain>/mcp/<secret>`.

The URL is permanent: it changes only if you change the hostname or the secret. YouTube requests go out from your PC, so YouTube sees your home IP.

```
Claude ──HTTPS──► Cloudflare ──tunnel──► [tunnel] ──► [mcp] ──your home IP──► YouTube
```

## 1. WSL prerequisites (PowerShell)

```powershell
wsl --update
```

```powershell
wsl -l -v
```

Your distro should show `VERSION 2`. If it shows 1, run `wsl --set-version Ubuntu 2`. The steps below assume your distro is named `Ubuntu`; use the name `wsl -l -v` shows.

## 2. Enable systemd in WSL (Ubuntu shell)

systemd lets Docker start automatically whenever WSL starts.

```bash
printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
```

Then, in PowerShell:

```powershell
wsl --shutdown
```

Open Ubuntu again and check that systemd is running. This should print `systemd`:

```bash
ps -p 1 -o comm=
```

## 3. Install Docker Engine inside WSL

Docker Desktop also works. If you already use it, skip this step and turn on *Settings → General → Start Docker Desktop when you sign in*. The rest of the guide is the same.

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
```

Review the script before running it:

```bash
less get-docker.sh
```

```bash
sudo sh get-docker.sh
```

```bash
sudo usermod -aG docker $USER
```

```bash
sudo systemctl enable --now docker
```

Close the Ubuntu window and open it again so your user picks up the `docker` group, then check:

```bash
docker run --rm hello-world
```

## 4. Get the code

Clone into the Linux filesystem (`~`), not `/mnt/c`. Builds are much faster there.

```bash
cd ~ && git clone https://github.com/harisharul/youtube-transcript.git
```

```bash
cd ~/youtube-transcript
```

## 5. Put your domain on Cloudflare (one time)

1. Sign up at dash.cloudflare.com (the Free plan is enough), click **Add a domain**, and enter one of your unused domains.
2. Cloudflare shows two nameservers. At your registrar (GoDaddy, Namecheap, BigRock, etc.), replace the domain's nameservers with those two.
3. Wait for Cloudflare to mark the domain **Active**. This usually takes minutes but can take up to 24 hours.

If the domain has email set up (MX records), check that Cloudflare imported those records before you switch nameservers.

## 6. Create the named tunnel

1. In the Cloudflare dashboard, go to **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared**. Menu names change from time to time; look for "Tunnels".
2. Name it `yt-mcp`.
3. On the install screen, choose **Docker**. Copy only the long token string after `--token`. Don't run the command; Compose runs `cloudflared` for you.
4. Click **Next** and add a public hostname:
   - **Subdomain:** `ytmcp` (or anything you like). **Domain:** your domain.
   - **Service URL:** `http://mcp:8000`. On older versions of the screen, set **Type** to `HTTP` and **URL** to `mcp:8000`.
   - **Path:** leave empty.

   `mcp` is the name of the server container on the Compose network. The URL is plain `http` because that hop stays inside Docker; Cloudflare provides HTTPS to Claude. You don't need a Host header override in this setup.
5. Save.

## 7. Configure secrets

```bash
cp .env.example .env && chmod 600 .env
```

```bash
sed -i "s/^MCP_SECRET_PATH=.*/MCP_SECRET_PATH=$(openssl rand -hex 24)/" .env
```

```bash
nano .env
```

In `nano`, paste the token after `TUNNEL_TOKEN=`, then save with Ctrl+O, Enter, Ctrl+X. `.env` is git-ignored, so it won't be committed.

## 8. Start it

```bash
docker compose up -d --build
```

```bash
docker compose ps
```

Both services should show `Up`, and `mcp` should show `(healthy)`. The tunnel log should mention `Registered tunnel connection`:

```bash
docker compose logs tunnel | tail
```

## 9. Test the public URL

Build your connector URL. Replace `ytmcp.example.in` with your hostname:

```bash
echo "https://ytmcp.example.in/mcp/$(grep ^MCP_SECRET_PATH= .env | cut -d= -f2)"
```

Then test it with a real MCP request. Use your own URL in place of `<URL>`. A working server returns `200`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST '<URL>' -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

Don't test by opening the URL in a browser. A GET on the MCP endpoint opens a stream and looks like it hangs.

## 10. Add it to Claude

1. Go to claude.ai → **Settings → Connectors → Add custom connector**.
2. Enter a name and the URL from step 9. Leave the OAuth fields empty, then click **Add**.
3. In a chat, turn the connector on from the tools menu.

It also works in the Claude mobile apps.

## 11. Start automatically when you log in to Windows

Both containers use `restart: unless-stopped` and Docker is enabled under systemd, so they come back whenever WSL starts. WSL itself doesn't start with Windows, and it shuts down when idle. A scheduled task that starts WSL with a long-running process fixes both. Run in PowerShell:

```powershell
$a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu --exec sleep infinity"; $t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Register-ScheduledTask -TaskName "WSL keep-alive (YouTube MCP)" -Action $a -Trigger $t -Settings $s
```

The `-AllowStartIfOnBatteries` flags only matter on a laptop. After a reboot and login, wait about 30 seconds and check from Ubuntu:

```bash
docker compose -f ~/youtube-transcript/docker-compose.yml ps
```

Also stop Windows from sleeping while plugged in (**Settings → System → Power**). The connector only works while the PC is awake.

## Day-to-day

| Task | Command (in `~/youtube-transcript`) |
|---|---|
| Update code and yt-dlp | `git pull && docker compose up -d --build` |
| View logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Rotate the secret | Edit `MCP_SECRET_PATH` in `.env`, run `docker compose up -d`, then update the connector URL in Claude |

## Hardening (optional)

- **Restrict who can reach the hostname.** Add a Cloudflare WAF custom rule for your hostname that blocks requests not coming from Anthropic's published outbound IP ranges (see Anthropic's "IP addresses" documentation). Then a leaked URL alone isn't enough to use the server.
- **Mount cookies as a file.** If YouTube ever demands sign-in, uncomment the `cookies.txt` lines in `docker-compose.yml`. They mount the file read-only instead of baking it into the image.
- **Container hardening is already on:** a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and a non-root user (uid 10001).
