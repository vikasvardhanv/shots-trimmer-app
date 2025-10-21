# Shots Trimmer

Shots Trimmer is a production-ready Flask application for repurposing long-form YouTube videos into vertical shorts that are optimized for TikTok, Instagram Reels, and YouTube Shorts. The project ships with a terminal-inspired interface, a REST API for automation, OAuth-powered account management, and Docker assets for easy deployment.

## Highlights
- **Fast video processing** – `yt-dlp` downloads and MoviePy trims clips into 9:16 vertical shorts with optional reaction overlays.
- **Automation ready** – Issue secured API keys, monitor usage, and trigger conversions via `/api/v1/*` endpoints.
- **Modern authentication** – Google and GitHub OAuth with Flask-Login session management.
- **Deployment friendly** – Dockerfile, docker-compose, Gunicorn config, and Procfile included.
- **SEO conscious** – Rich meta tags, structured data, sitemap, and tuned robots directives baked in.
- **Operational hygiene** – Thread-safe job tracking with periodic cleanup and health checks for uptime monitoring.

## Prerequisites
- Python 3.11+
- FFmpeg available on the host (install via `brew install ffmpeg` on macOS or `apt-get install ffmpeg` on Debian/Ubuntu)
- A writable filesystem for temporary video artifacts (`downloads/`, `uploads/`, `logs/` are created automatically)

## Quick Start (Local)
1. **Create a virtual environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. **Install dependencies**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env and provide secrets (see table below)
   ```
4. **Run the server**
   ```bash
   flask --app app run --host 0.0.0.0 --port 5000
   ```
   The database schema is created automatically on first launch.

## Environment Variables
| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | random fallback | Flask session encryption/signing key |
| `API_KEY` | _(empty)_ | Admin API key for bypassing per-user keys |
| `DATABASE_URL` | `sqlite:///instance/app.db` | SQLAlchemy connection string (supports PostgreSQL, MySQL, etc.) |
| `MAX_CONTENT_LENGTH` | `104857600` | Upload cap in bytes (100 MB) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | _(empty)_ | Enable Google OAuth sign-in |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | _(empty)_ | Enable GitHub OAuth sign-in |
| `PORT` | `5000` | Runtime port (used by Gunicorn/Docker) |
| `WEB_CONCURRENCY` | `1` | Gunicorn worker count override |

> **Note:** `.env` is ignored by git. Copy from `.env.example`, fill in secure values, and keep it out of version control.

## Running with Docker
```bash
docker compose up --build
```
- Gunicorn serves the app on `:8000` inside the container.
- Optional Redis and Nginx services are defined but disabled by default; uncomment in `docker-compose.yml` when needed.
- Health checks hit `/health` every 30 seconds.

## REST API Overview
All API endpoints require an `X-API-Key` header. Keys can be generated through the web UI or programmatically.

- `POST /api/v1/convert` – start a conversion job
- `GET /api/v1/status/<job_id>` – check job progress
- `GET /api/v1/download/<job_id>/<short_index>` – download a processed short
- `GET /api/v1/jobs` – list recent jobs
- `DELETE /api/v1/cleanup/<job_id>` – purge cached artifacts

Example conversion request:
```bash
curl -X POST https://your-domain.com/api/v1/convert \
  -H "X-API-Key: st_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "num_shorts": 3,
    "clip_duration": 30,
    "add_reaction": false
  }'
```

## Production Notes
- Use Gunicorn via the supplied `Procfile` or Docker image (`gunicorn --config gunicorn.conf.py app:app`).
- Configure a persistent database (`DATABASE_URL`) in production; SQLite is provided only for local development.
- Set `SESSION_COOKIE_SECURE=1` and serve over HTTPS in live environments.
- Point your reverse proxy (Nginx example included) at the Gunicorn service and terminate TLS there.
- Schedule regular cleanup of temporary media if you disable the built-in background cleaner.

## SEO & Growth Essentials
- `templates/base.html` defines rich OpenGraph/Twitter cards, canonical URLs, and schema.org structured data.
- `static/robots.txt` and the dynamic `/sitemap.xml` expose crawl-friendly entry points.
- Page-level templates override meta descriptions to target relevant keywords such as "YouTube shorts generator" and "video repurposing automation".

## Maintenance Checklist
- Monitor `/health` for uptime and container health checks.
- Review API usage via the dashboard (`/api-dashboard`) and the `ApiUsage` table.
- Rotate API keys or revoke them directly from the database if compromised.
- Keep `yt-dlp` and FFmpeg up to date for compatibility with YouTube changes.

Happy trimming!
