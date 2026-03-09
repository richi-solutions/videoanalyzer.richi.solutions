# Architecture Overview — videoanalyzer.richi.solutions

## System Summary

VideoAnalyzer is a stateless microservice that bridges public video URLs and the Google Gemini AI API. It accepts analysis requests via HTTP, offloads the long-running work to a background task, and persists job state in Firestore so callers can poll for results.

The service is deployed as a Docker container on Google Cloud Run (serverless, scales to zero).

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────┐
│                    HTTP Clients                      │
│        (hookr.richi.solutions, n8n, curl)            │
└───────────────────┬─────────────────────────────────┘
                    │  X-API-Key header
                    ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI (app/main.py)                │
│                                                      │
│  POST /api/analyze   GET /api/jobs/{id}   GET /health│
└───────┬──────────────────────┬──────────────────────┘
        │                      │
        ▼                      ▼
┌───────────────┐   ┌──────────────────────┐
│   auth.py     │   │       jobs.py         │
│               │   │                      │
│ - Firestore   │   │ - create_job()        │
│   key lookup  │   │ - update_job()        │
│ - In-mem cache│   │ - get_job()           │
│ - Rate limit  │   │ - Stale detection    │
└───────────────┘   └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │     analyze.py        │
                    │  (background task)    │
                    │                      │
                    │  0. Validate URL      │
                    │  1. Check duration   │
                    │  2. yt-dlp download  │
                    │  3. Gemini upload    │
                    │  4. Poll ACTIVE state │
                    │  5. Generate content │
                    │  6. Cleanup tmp file │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌──────────────┐  ┌─────────────┐  ┌──────────────┐
    │   yt-dlp     │  │  Gemini     │  │  Firestore   │
    │  (download)  │  │  File API   │  │  (job state) │
    └──────────────┘  └─────────────┘  └──────────────┘
```

---

## Data Flow

### Successful analysis request

1. Client sends `POST /api/analyze` with `{ url, prompt? }` and `X-API-Key` header.
2. `auth.py` validates the key against Firestore (cached 5 min) and checks rate limit.
3. `main.py` validates the request body and calls `create_job()` in Firestore (`status: pending`).
4. FastAPI returns `202 { jobId, status: "pending" }` immediately.
5. Background task `process_job()` starts:
   - Updates job to `status: processing`.
   - Validates URL format; fetches video metadata (duration check, no download).
   - Downloads video to a temporary file via yt-dlp (max 500 MB).
   - Uploads the file to the Gemini File API.
   - Polls `client.files.get()` every 5 seconds until state is `ACTIVE` (max 2 min).
   - Calls `client.models.generate_content()` with the uploaded file and prompt.
   - Updates job to `status: completed, result: { analysis, model }`.
6. Client polls `GET /api/jobs/{jobId}` until `status` is `completed` or `failed`.

### Stale job recovery

Cloud Run can throttle CPU on background tasks after the HTTP response is sent. If a job stays in `processing` for more than 5 minutes, `get_job()` detects this on read and marks the job `failed` with code `JOB_STALE`.

---

## Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `app/main.py` | FastAPI application, route handlers, request/response shaping, structured logging |
| `app/analyze.py` | Video analysis pipeline: URL validation, yt-dlp download, Gemini upload/poll/generate, temp file cleanup |
| `app/auth.py` | API key authentication (Firestore multi-key + `SERVICE_API_KEY` fallback), in-memory cache, sliding window rate limiter |
| `app/jobs.py` | Firestore CRUD for jobs (`create`, `update`, `get`), stale job detection |
| `app/manage_keys.py` | CLI for managing API keys in Firestore (create, list, revoke) |

---

## External Service Integrations

### Google Gemini API

- **SDK:** `google-genai >= 1.0.0`
- **File API:** Used to upload video files before analysis. Files must reach `ACTIVE` state before content generation.
- **Content generation:** `client.models.generate_content()` with the uploaded file URI and a text prompt.
- **Model:** Configurable via `GEMINI_MODEL` env var; defaults to `gemini-2.0-flash`.

### Google Cloud Firestore

- **SDK:** `google-cloud-firestore >= 2.16.0`
- **Collections:**
  - `jobs` — one document per analysis request, updated through the job lifecycle.
  - `api_keys` — API key records with `key_hash`, `name`, `rate_limit`, `active` fields.
- **Auth:** Application Default Credentials (ADC) on Cloud Run; set `GCP_PROJECT_ID` if the default project differs.

### yt-dlp

- Invoked synchronously in a thread pool (`asyncio.to_thread`) to avoid blocking the event loop.
- Configured with: mp4+m4a format preference, 500 MB size cap, browser-like headers for bot detection bypass.
- Pre-download metadata extraction for duration validation (no download, separate yt-dlp call).

---

## Firestore Schema

### Collection: `jobs`

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `pending` / `processing` / `completed` / `failed` |
| `url` | string | Source video URL |
| `prompt` | string | Analysis prompt used |
| `created_at` | string (ISO 8601) | Job creation timestamp |
| `updated_at` | string (ISO 8601) | Last status update timestamp |
| `result` | map / null | `{ analysis: string, model: string }` on success |
| `error` | map / null | `{ code: string, message: string }` on failure |

### Collection: `api_keys`

| Field | Type | Description |
|-------|------|-------------|
| `key_hash` | string | SHA-256 hash of the raw API key |
| `name` | string | Human-readable identifier (e.g., `hookr-production`) |
| `rate_limit` | int | Maximum requests per minute for this key |
| `active` | bool | False for revoked keys |
| `created_at` | string (ISO 8601) | Key creation timestamp |

---

## Key Architectural Decisions

### Asynchronous job model

Cloud Run has a request timeout. A direct synchronous approach (download + Gemini analysis in the request handler) would time out on longer videos. The async job model decouples response time from processing time.

**Trade-off:** Requires a persistent store (Firestore) and client-side polling. Stale job detection compensates for Cloud Run CPU throttling on background tasks.

### Blocking I/O in thread pool

FastAPI runs on an async event loop. yt-dlp and the Gemini SDK are blocking. All blocking calls are wrapped in `asyncio.to_thread()` to avoid blocking the event loop while still using the async task model.

### SHA-256 key hashing

Raw API keys are never stored in Firestore — only their SHA-256 hashes. This limits exposure if the Firestore collection is compromised. The raw key is shown only once at creation time.

### In-memory API key cache

Firestore lookups on every request would be expensive. A 5-minute TTL in-memory cache (`_key_cache` dict) reduces Firestore reads significantly under load. The trade-off is up to 5 minutes of lag when a key is revoked.

### Legacy `SERVICE_API_KEY` fallback

The TypeScript prototype used a single env-var key. The Python rewrite maintains backward compatibility by checking `SERVICE_API_KEY` first, enabling zero-downtime migration to multi-key Firestore auth.
