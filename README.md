<div align="center">

<img src="app/static/img/jellysniff.png" alt="JellySniff" width="220">

# JellySniff

**Stats, recommendations, and a live now-playing dashboard for [Jellyfin](https://jellyfin.org/), built straight on top of its SQLite databases.**

[![Build](https://github.com/flyersa/jellysniff/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/flyersa/jellysniff/actions/workflows/docker-publish.yml)
[![Image](https://img.shields.io/badge/ghcr.io-flyersa%2Fjellysniff-blue?logo=docker)](https://github.com/flyersa/jellysniff/pkgs/container/jellysniff)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

JellySniff sits next to your Jellyfin install, reads the local SQLite files
**read-only**, and serves a dark dense reporting UI with per-user dashboards
and an admin overview. Same login as Jellyfin (auth is proxied — JellySniff
never sees or stores your password).

---

## What you get

**Admin overview**
- Library counts (movies, series, episodes, audiobooks)
- Live "Now Playing" widget with per-session bitrate + total bandwidth
- Daily / weekly / monthly watch-volume charts
- Sliding-window top items + top users
- Paginated "Recently Watched" feed (users link to drilldowns, titles link to item pages)
- Hour-of-week heatmap

**Per-user dashboard**
- Personal watch time / play count / device count / favorites
- Daily, monthly, hour-of-day charts of your own activity
- "Popular Now" — what's trending on the server you haven't seen yet
- "Just for You" recommendations with % match and explanation tags
- Series completion progress bars
- Full paginated history (50/page)

**Item detail pages**
- Per movie / series / episode: top viewers (admin only), episode-by-episode play counts
- Click any title in any table → drops you here

**Recommender**
- Hybrid scoring: a per-item content vector (genres + studios + tags, IDF-weighted)
  combined with implicit-feedback SVD over everyone's watch history
- Episodes roll up to their series so the suggestion list reads "Stargate SG-1"
  rather than "S03E07"
- German↔English genre folding (Abenteuer → Adventure, Komödie → Comedy, …)
  so the model doesn't split duplicate labels

**Privacy**
- Non-admin users never see other users' account names anywhere (peer
  reasons collapse to "N other users watched", the peers-with-similar-taste
  section is hidden entirely)
- All UI copy is plain English — no algorithm jargon leaked

---

## Quick start (Docker — recommended)

Pull the prebuilt image from GitHub Container Registry and run it. The image
is published from `main` and supports `linux/amd64` + `linux/arm64`.

```bash
mkdir jellysniff && cd jellysniff

# 1. Create .env (NEVER commit this)
cat > .env <<EOF
JS_JELLYFIN_URL=http://host.docker.internal:8096
JS_JELLYFIN_VERIFY_SSL=false
JS_JELLYFIN_API_KEY=
JS_SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
JS_SECURE_COOKIE=false
JS_ENABLE_HSTS=false
JS_TRUSTED_PROXIES=
JS_SESSION_MAX_AGE=43200
JS_BRAND_NAME=JellySniff
JS_ACCENT_COLOR=#AA5CC3
EOF
chmod 600 .env

# 2. Run
docker run -d --name jellysniff \
  --restart unless-stopped \
  --add-host host.docker.internal:host-gateway \
  -p 8095:8095 \
  -v /var/lib/jellyfin/data:/data:ro \
  -v jellysniff_cache:/cache \
  --env-file .env \
  ghcr.io/flyersa/jellysniff:latest

# 3. Browse
xdg-open http://localhost:8095
```

That's it. Sign in with any Jellyfin account. Admins land on the overview,
everyone else lands on their personal dashboard.

### Or with Docker Compose

```yaml
services:
  jellysniff:
    image: ghcr.io/flyersa/jellysniff:latest
    container_name: jellysniff
    restart: unless-stopped
    ports: ["8095:8095"]
    env_file: .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - /var/lib/jellyfin/data:/data:ro
      - jellysniff_cache:/cache
volumes:
  jellysniff_cache:
```

---

## Configuration

All settings are env vars prefixed `JS_`. See `.env.example` for the full list.
The important ones:

| Variable | Required | Notes |
|---|---|---|
| `JS_JELLYFIN_URL` | ✅ | Where the app reaches Jellyfin. Use `http://host.docker.internal:8096` when both run on the same Docker host. |
| `JS_SESSION_SECRET` | ✅ | Refuses to start if blank or a placeholder. Generate with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. |
| `JS_JELLYFIN_API_KEY` | optional | Most Jellyfin installs serve item images publicly on the local network, so this can be blank. Mint one in *Dashboard → API Keys* if your server requires auth on `/Sessions` or image endpoints. |
| `JS_SECURE_COOKIE` | optional | Defaults to `true` (cookie is HTTPS-only). Flip to `false` for plain-HTTP testing on a LAN. |

---

## Requirements

- A running Jellyfin server, **with the [Playback Reporting plugin](https://github.com/jellyfin/jellyfin-plugin-playbackreporting) installed and enabled** (the rich watch timeline + bitrate data lives in `playback_reporting.db`).
- The Jellyfin data directory mountable into the container at `/data` (default `/var/lib/jellyfin/data`).
- Docker 20.10+ (needed for `--add-host …:host-gateway`).

---

## Security

- Both SQLite files mounted **read-only**; connections opened with
  `mode=ro&cache=shared` plus `PRAGMA query_only=1`.
- Auth proxied to Jellyfin's `/Users/AuthenticateByName` — PBKDF2 hashes never touched.
- Signed `itsdangerous` session cookie, `HttpOnly`, `SameSite=Lax`.
- CSP `default-src 'self'` with per-request script nonces; `unsafe-inline`
  eliminated for scripts. `frame-ancestors 'none'`. HSTS sent when the
  cookie is configured `Secure`.
- Login throttle (8/min/IP); `X-Forwarded-For` is only honoured from
  configured trusted proxies.
- App refuses to start if `JS_SESSION_SECRET` is missing, blank, or a placeholder.

---

## Building from source

```bash
git clone https://github.com/flyersa/jellysniff.git
cd jellysniff
docker build -t jellysniff:dev .
```

The Dockerfile is multi-stage: a Debian-slim asset stage pulls the
Tailwind v4 standalone CLI plus htmx / Alpine / Chart.js at the versions
pinned at the top, runs the Tailwind build, and the runtime stage installs
only the Python deps from `requirements.txt`. Final image is ~200 MB and
runs as a non-root user.

---

## License

MIT — see [LICENSE](LICENSE).
