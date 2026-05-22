# Deployment Recipes

Copy-paste docker-compose snippets for common homelab setups.

---

## Jellyfin and Lidarr on the same Docker host (different network)

The most common homelab setup: Jellyfin and Lidarr are on a `homelab` network,
`octave` creates its own `octave_default` network.

Use the Docker host's **gateway IP** on the `homelab` network — typically
`172.18.0.1`, but check with:

```bash
docker network inspect homelab | grep Gateway
```

**.env:**
```env
JELLYFIN_URL=http://172.18.0.1:8096
LIDARR_URL=http://172.18.0.1:8686
```

No changes to `docker-compose.yml` needed.

---

## Jellyfin and Lidarr on the same Docker network

Join all three containers to the same network so they can reach each other
by service name.

**docker-compose.yml** (octave side):
```yaml
services:
  octave:
    build: .
    image: octave:local
    networks:
      - default
      - homelab          # join the existing network
    environment:
      JELLYFIN_URL: http://jellyfin:8096
      LIDARR_URL:   http://lidarr:8686
      # ... rest of env vars

networks:
  homelab:
    external: true       # must already exist: docker network create homelab
```

Then make sure Jellyfin and Lidarr are also on the `homelab` network.

---

## Automatic daily sync at a custom time

In `.env`:
```env
TZ=America/New_York       # or your timezone
SYNC_SCHEDULE=0 3 * * *   # 03:00 every night in New_York time
```

Disable automatic sync entirely:
```env
SYNC_SCHEDULE=            # empty string = disabled
```

---

## Sync on startup only (no ongoing schedule)

```env
SYNC_ON_STARTUP=true
SYNC_SCHEDULE=            # disabled
```

---

## Run as a one-shot cron job (no web server)

Useful if you want an external cron controller (e.g. Portainer schedules or
host cron) instead of the built-in scheduler.

```env
SYNC_MODE=oneshot
```

Cron entry on the host (runs at 02:00 daily):
```cron
0 2 * * * cd /home/jack/octave && docker compose run --rm octave
```

---

## Behind an nginx reverse proxy

Expose the web UI publicly with HTTPS termination.

**nginx config snippet:**
```nginx
server {
    listen 443 ssl;
    server_name sync.example.com;

    ssl_certificate     /etc/letsencrypt/live/sync.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sync.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;

        # Required for SSE log streaming
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;
    }
}
```

**Recommended: set a password** when exposing publicly:
```env
AUTH_USERNAME=octave
AUTH_PASSWORD=a-long-random-string-here
```
The browser will prompt using HTTP Basic Auth.

---

## Behind Traefik (Docker labels)

```yaml
services:
  octave:
    build: .
    image: octave:local
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.octave.rule=Host(`sync.example.com`)"
      - "traefik.http.routers.octave.entrypoints=websecure"
      - "traefik.http.routers.octave.tls.certresolver=letsencrypt"
      - "traefik.http.services.octave.loadbalancer.server.port=8000"
    networks:
      - traefik-public
      - default
    environment:
      # ... your env vars
```

---

## Tailscale (expose only on your tailnet)

No special config needed — just bind the host to your Tailscale IP and access
the service from any Tailscale device.

```env
# In docker-compose.yml environment, or .env:
WEB_PORT=8000
```

Then access at `http://<tailscale-ip>:8000/`. No firewall rules needed since
Tailscale handles its own routing.

For a stable URL, use a Tailscale funnel or set a DNS record in Tailscale
admin pointing to the host.

---

## Adding HTTP Basic Auth

Even on a LAN, you may want to protect the API from other devices:

```env
AUTH_USERNAME=octave
AUTH_PASSWORD=change-me-to-something-random
```

Generate a random key:
```bash
openssl rand -hex 32
```

The browser stores the Basic Auth session for the current tab/profile.

---

## Debug mode (verbose match logging)

```env
LOG_LEVEL=DEBUG
```

Then tail:
```bash
make logs
# or filter for just match decisions:
docker compose logs -f | grep -E 'match|score|missing|WARN'
```

Disable after diagnosing — DEBUG mode is chatty (~10× the log volume).

---

## Volume layout reference

| Host path | Container path | Notes |
|---|---|---|
| `./config/config.json` | `/app/config/config.json` | Playlist list + match thresholds. Editable from the UI. |
| `./data/.spotify_pkce_token` | `/app/data/.spotify_pkce_token` | OAuth refresh token. Keep secret. chmod 600 where supported. |
| `./data/sync_state.json` | `/app/data/sync_state.json` | Lidarr request state machine. Don't delete unless resetting. |
| `./data/octave.db` | `/app/data/octave.db` | SQLite run history. Safe to delete to reset history. |
| `./logs/octave.log` | `/app/logs/octave.log` | Application log. Rotated by the host or a logrotate sidecar. |
