# videoanalyzer — Claude Instructions

## What this repo is

Platform-agnostic video analysis microservice. Accepts a video URL, downloads it via
yt-dlp, uploads to the Gemini File API, and returns an AI-generated analysis.
Called by other services (e.g. hookr) via authenticated HTTP.

Uses an async job pattern: POST returns a job ID immediately, processing happens in the
background, clients poll GET /api/jobs/{id} for the result.

## API Contract

```
POST /api/analyze
Headers: X-API-Key: <SERVICE_API_KEY>
Body:    { "url": "https://...", "prompt": "optional custom prompt" }
Response: 202 { "ok": true, "data": { "jobId": "uuid", "status": "pending" } }

GET /api/jobs/{job_id}
Headers: X-API-Key: <SERVICE_API_KEY>
Response: { "ok": true, "data": { "jobId": "...", "status": "pending|processing|completed|failed", "result"?: {...}, "error"?: {...} } }

GET /health  (no auth)
Response: { "ok": true, "data": { "status": "healthy" } }
```

## Stack

- **Runtime:** Python 3.12
- **Framework:** FastAPI + Uvicorn
- **Video download:** yt-dlp (native Python library)
- **AI:** Google Gemini File API (`google-genai` SDK)
- **Job storage:** Google Cloud Firestore
- **Rate limiting:** slowapi (60 req/min per API key)
- **Deployment:** Google Cloud Run (Docker, auto-deploy via Cloud Build)

## Project Structure

```
app/
  main.py        — FastAPI app, route definitions, rate limiting
  analyze.py     — Core logic: download → upload to Gemini → poll → generate
  auth.py        — API key validation (X-API-Key header)
  jobs.py        — Firestore CRUD for async job state
Dockerfile       — python:3.12-slim + ffmpeg
cloudbuild.yaml  — Cloud Build pipeline for auto-deploy
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | — | Google Gemini API key |
| `SERVICE_API_KEY` | Yes | — | Shared secret for X-API-Key auth |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model to use |
| `GCP_PROJECT_ID` | No | auto-detect | GCP project for Firestore |
| `PORT` | No | `8080` | HTTP port |

## Error Codes

| Code | HTTP | Meaning |
|------|------|---------|
| `INVALID_INPUT` | 400 | Missing or invalid request body |
| `UNAUTHORIZED` | 401 | Missing or wrong X-API-Key |
| `RATE_LIMITED` | 429 | Too many requests |
| `DOWNLOAD_FAILED` | 422 | yt-dlp could not download the URL |
| `GEMINI_TIMEOUT` | 504 | File processing exceeded 2 minutes |
| `GEMINI_FILE_FAILED` | 502 | Gemini file processing failed |
| `MISCONFIGURATION` | 500 | Missing required env var |
| `INTERNAL_ERROR` | 500 | Unexpected error |
| `NOT_FOUND` | 404 | Job or endpoint not found |

## Conventions

- All responses follow Error Envelope: `{ ok, data? }` or `{ ok, error: { code, message }, traceId }`
- Errors are thrown as `AppError(code, message)` — never raw exceptions
- Temp files always cleaned up in `finally` block
- No secrets in code — env vars only
- Blocking calls (yt-dlp, Gemini uploads) offloaded via `asyncio.to_thread()`

## Build & Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Deployment

Cloud Build auto-deploys on push to `main` via cloudbuild.yaml.
Docker build installs ffmpeg in the runtime stage.
Cloud Run service: `videoanalyzer-richi-solutions` in `europe-west3`.
Health check: `GET /health` must return 200.

## Notes for Claude Code

- No frontend, no Lovable, no Supabase
- The Consumer-Pro KB (React/frontend rules) does NOT apply here
- Global ~/.claude standards apply: Conventional Commits, Error Envelope, English comments
