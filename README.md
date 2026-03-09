# videoanalyzer.richi.solutions

Platform-agnostic video analysis microservice that downloads any public video via yt-dlp and analyzes it using the Google Gemini API.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [API Endpoints](#api-endpoints)
- [API Key Management](#api-key-management)
- [Deployment](#deployment)
- [Documentation](#documentation)

---

## Overview

VideoAnalyzer accepts a public video URL (YouTube, TikTok, Instagram, and any platform supported by yt-dlp), downloads it, uploads it to the Gemini File API, and returns an AI-generated analysis. Jobs are processed asynchronously — the POST endpoint returns a job ID immediately, and callers poll the status endpoint until the result is ready.

The service exposes a simple JSON REST API protected by API keys stored in Firestore.

---

## Features

- Download videos from any yt-dlp-supported platform (YouTube, TikTok, Instagram, Vimeo, etc.)
- Analyze video content with a configurable Gemini model (default: `gemini-2.0-flash`)
- Custom analysis prompts per request
- Pre-download duration check — rejects videos over 10 minutes before downloading
- Asynchronous job processing with Firestore-backed state (`pending` → `processing` → `completed`/`failed`)
- Multi-key API authentication with per-key rate limiting (sliding window, configurable req/min)
- In-memory key cache (5-minute TTL) to reduce Firestore reads
- Backwards-compatible single-key mode via `SERVICE_API_KEY` env var
- Stale job detection — jobs stuck in `processing` for over 5 minutes are auto-failed
- Structured JSON logging for Cloud Run log aggregation
- Error envelope standard on all responses (`ok`, `data`/`error`, `traceId`)
- CLI tool for API key lifecycle management (create, list, revoke)

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Runtime** | Python 3.12 |
| **Web Framework** | FastAPI + Uvicorn |
| **Video Download** | yt-dlp |
| **AI Analysis** | Google Gemini API (`google-genai`) |
| **Job Storage** | Google Cloud Firestore |
| **Containerization** | Docker (python:3.12-slim + ffmpeg) |
| **CI/CD** | Google Cloud Build |
| **Deployment** | Google Cloud Run (europe-west3) |

---

## Architecture

The service follows a single-responsibility microservice pattern with asynchronous job processing.

```
Client
  │
  │  POST /api/analyze  (X-API-Key header)
  ▼
FastAPI (app/main.py)
  │
  ├── auth.py          — API key validation (Firestore + in-memory cache) + rate limiting
  ├── jobs.py          — Firestore job CRUD (create / update / get)
  └── analyze.py       — Core pipeline (background task)
        │
        ├── Step 0: Validate URL + check video duration (yt-dlp metadata, no download)
        ├── Step 1: Download video to tmp file (yt-dlp, max 500 MB)
        ├── Step 2: Upload to Gemini File API
        ├── Step 3: Poll until file state = ACTIVE (max 2 min)
        ├── Step 4: Generate content via Gemini model
        └── Cleanup: Delete tmp file (always, via finally block)
```

**Job lifecycle:**

```
POST /api/analyze  →  job created (pending)  →  202 { jobId }
                           │
                     background task starts
                           │
                    status = processing
                           │
              ┌────────────┴────────────┐
           success                   failure
              │                         │
      status = completed         status = failed
      result = { analysis, model }   error = { code, message }
```

**Authentication modes:**

- **Multi-key (Firestore):** Keys stored as SHA-256 hashes in the `api_keys` collection with per-key rate limits.
- **Legacy (env var):** If `SERVICE_API_KEY` is set and matches, the request is admitted with a 60 req/min limit.

---

## Getting Started

### Prerequisites

- Python 3.12+
- `ffmpeg` installed and on PATH (required by yt-dlp for stream merging)
- A Google Cloud project with Firestore enabled
- A Gemini API key from [Google AI Studio](https://aistudio.google.com)

### Installation

```bash
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `SERVICE_API_KEY` | No | Legacy single-key auth (fallback if Firestore keys are not set up) |
| `GEMINI_MODEL` | No | Gemini model name (default: `gemini-2.0-flash`) |
| `GCP_PROJECT_ID` | No | GCP project for Firestore (uses ADC default if omitted) |
| `PORT` | No | Server port (default: `8080`; Railway/Cloud Run inject this automatically) |

### Running Locally

```bash
uvicorn app.main:app --reload --port 8000
```

---

## Project Structure

```
videoanalyzer.richi.solutions/
├── app/
│   ├── main.py          # FastAPI app, route definitions, request validation
│   ├── analyze.py       # Core video analysis pipeline (download → upload → generate)
│   ├── auth.py          # API key middleware (Firestore multi-key + rate limiting)
│   ├── jobs.py          # Firestore job CRUD and stale-job detection
│   └── manage_keys.py   # CLI tool for API key lifecycle management
├── src/                 # Deprecated TypeScript prototype (not used by Dockerfile)
├── skills/              # Claude Code skill definitions
├── Dockerfile           # python:3.12-slim + ffmpeg
├── cloudbuild.yaml      # Cloud Build CI/CD pipeline (build → push → Cloud Run deploy)
├── railway.toml         # Railway deployment config (alternative to Cloud Run)
├── requirements.txt     # Python dependencies
└── .env.example         # Environment variable template
```

---

## API Endpoints

All protected endpoints require an `X-API-Key` header.

### `GET /health`

Health check. No authentication required.

**Response `200`:**
```json
{ "ok": true, "data": { "status": "healthy" } }
```

---

### `POST /api/analyze`

Submit a video for analysis. Returns a job ID immediately; processing runs in the background.

**Request body:**
```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "prompt": "Summarize the key points discussed."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | Public video URL (any yt-dlp-supported platform) |
| `prompt` | string | No | Analysis instruction (default: describe visuals and audio) |

**Response `202`:**
```json
{
  "ok": true,
  "data": { "jobId": "uuid", "status": "pending" }
}
```

**Error responses:**

| HTTP | Code | Cause |
|------|------|-------|
| 400 | `INVALID_INPUT` | Empty URL or invalid URL format |
| 401 | `UNAUTHORIZED` | Missing or invalid `X-API-Key` |
| 429 | `RATE_LIMITED` | Per-key rate limit exceeded |
| 500 | `INTERNAL_ERROR` | Job creation failed |

---

### `GET /api/jobs/{job_id}`

Poll the status and result of an analysis job.

**Response `200` (pending/processing):**
```json
{
  "ok": true,
  "data": {
    "jobId": "uuid",
    "status": "processing",
    "createdAt": "2024-01-01T12:00:00+00:00",
    "updatedAt": "2024-01-01T12:00:05+00:00"
  }
}
```

**Response `200` (completed):**
```json
{
  "ok": true,
  "data": {
    "jobId": "uuid",
    "status": "completed",
    "createdAt": "...",
    "updatedAt": "...",
    "result": {
      "analysis": "The video shows...",
      "model": "gemini-2.0-flash"
    }
  }
}
```

**Response `200` (failed):**
```json
{
  "ok": true,
  "data": {
    "jobId": "uuid",
    "status": "failed",
    "error": { "code": "DOWNLOAD_FAILED", "message": "..." }
  }
}
```

**Job error codes:**

| Code | Cause |
|------|-------|
| `DOWNLOAD_FAILED` | yt-dlp could not download the video |
| `VIDEO_TOO_LONG` | Video duration exceeds 10-minute limit |
| `GEMINI_TIMEOUT` | Gemini file processing exceeded 2-minute poll timeout |
| `GEMINI_FILE_FAILED` | Gemini rejected the uploaded file |
| `MISCONFIGURATION` | `GEMINI_API_KEY` not set |
| `INTERNAL_ERROR` | Unexpected runtime error |
| `JOB_STALE` | Background task killed by Cloud Run CPU throttling |

---

## API Key Management

Use the built-in CLI to manage API keys in Firestore:

```bash
# Create a new key with a custom rate limit
python -m app.manage_keys create --name hookr-production --rate-limit 100

# List all keys
python -m app.manage_keys list

# Revoke a key by name
python -m app.manage_keys revoke --name hookr-production
```

Keys are stored as SHA-256 hashes in the `api_keys` Firestore collection. The raw key is only shown once at creation — it cannot be retrieved afterward.

---

## Deployment

### Google Cloud Run (primary)

Cloud Build triggers on push to `main`, builds a Docker image, pushes to Artifact Registry, and deploys to Cloud Run in `europe-west3`.

```bash
# Manual trigger
gcloud builds submit --config cloudbuild.yaml
```

Required Cloud Run environment variables: `GEMINI_API_KEY`, `GCP_PROJECT_ID`.

### Railway (alternative)

`railway.toml` is configured with Dockerfile builds, health check on `/health`, and restart-on-failure policy. Set environment variables in the Railway dashboard.

---

## Documentation

| Document | Location |
|----------|----------|
| Architecture Overview | `docs/architecture/overview.md` |
| Contributing Guide | `CONTRIBUTING.md` |
| API Documentation | Not yet generated — run api-docs agent |
