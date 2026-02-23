# videoanalyzer — Claude Instructions

## What this repo is

Platform-agnostic video analysis microservice. Accepts a video URL, downloads it via
yt-dlp, uploads to the Gemini File API, and returns an AI-generated analysis.
Called by other services (e.g. hookr) via authenticated HTTP.

## API Contract

```
POST /api/analyze
Headers: X-API-Key: <SERVICE_API_KEY>
Body:    { "url": "https://...", "prompt": "optional custom prompt" }

Response (success):  { "ok": true, "data": { "analysis": "...", "model": "..." } }
Response (error):    { "ok": false, "error": { "code": "...", "message": "..." }, "traceId": "..." }
```

Health check (no auth): `GET /health` → `{ "ok": true, "data": { "status": "healthy" } }`

## Stack

- **Runtime:** Node.js 22 (TypeScript, compiled to `dist/`)
- **Framework:** Express 4
- **Video download:** yt-dlp (system binary via pip, path: `/usr/local/bin/yt-dlp`)
- **AI:** Google Gemini File API (`@google/generative-ai`)
- **Package manager:** npm
- **Deployment:** Railway (Docker, see `railway.toml`)

## Project Structure

```
src/
  server.ts          — Express app, route definitions, error handling
  analyze.ts         — Core logic: download → upload to Gemini → poll → generate
  middleware/
    auth.ts          — API key validation (X-API-Key header)
Dockerfile           — Multi-stage build: builder (tsc) + runtime (node + yt-dlp + ffmpeg)
railway.toml         — Railway deployment config (healthcheck: /health)
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | — | Google Gemini API key |
| `SERVICE_API_KEY` | Yes | — | Shared secret for X-API-Key auth |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model to use |
| `PORT` | No | `3000` | HTTP port |

## Error Codes

| Code | HTTP | Meaning |
|------|------|---------|
| `INVALID_INPUT` | 400 | Missing or invalid request body |
| `UNAUTHORIZED` | 401 | Missing or wrong X-API-Key |
| `DOWNLOAD_FAILED` | 422 | yt-dlp could not download the URL |
| `GEMINI_TIMEOUT` | 504 | File processing exceeded 2 minutes |
| `GEMINI_FILE_FAILED` | 502 | Gemini file processing failed |
| `MISCONFIGURATION` | 500 | Missing required env var |
| `INTERNAL_ERROR` | 500 | Unexpected error |

## Conventions

- All responses follow Error Envelope: `{ ok, data? }` or `{ ok, error: { code, message }, traceId }`
- Errors are thrown as `AppError(code, message)` — never raw exceptions
- Temp files always cleaned up in `finally` block
- No secrets in code — env vars only

## Build & Run

```bash
npm ci           # install deps
npm run build    # tsc → dist/
npm start        # node dist/server.js
npm run dev      # ts-node src/server.ts (local dev)
```

## Deployment

Railway auto-deploys on push to `main` via Dockerfile.
Docker build installs yt-dlp + ffmpeg in the runtime stage.
Health check: `GET /health` must return 200 within 300s.

## Notes for Claude Code

- This is a stateless microservice — no database, no session
- No frontend, no Lovable, no Supabase
- The Consumer-Pro KB (React/frontend rules) does NOT apply here
- Global ~/.claude standards apply: Conventional Commits, Error Envelope, English comments
