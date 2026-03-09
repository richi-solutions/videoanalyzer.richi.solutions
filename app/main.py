"""
FastAPI application entry point for the VideoAnalyzer service.

Defines three HTTP routes:
- GET  /health                — liveness probe, no auth
- POST /api/analyze           — submit a video URL for analysis (async job)
- GET  /api/jobs/{job_id}     — poll job status and retrieve results

Request validation, structured logging, and error envelope formatting are
handled in this module. The actual analysis pipeline runs as a FastAPI
background task via app/analyze.py.
"""

import json
import sys
import time
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.analyze import AppError, process_job, validate_url
from app.auth import require_api_key
from app.jobs import create_job, get_job

load_dotenv()


def _log(level: str, event: str, **data) -> None:
    """Emit a structured JSON log entry to stdout for Cloud Run log aggregation.

    Args:
        level: Severity string (e.g., ``"INFO"``, ``"ERROR"``).
        event: Short machine-readable event name (e.g., ``"job_accepted"``).
        **data: Arbitrary key-value pairs merged into the log entry.
    """
    entry = {"severity": level, "event": event, "ts": time.time(), **data}
    print(json.dumps(entry), flush=True, file=sys.stdout)


app = FastAPI(title="Video Analyzer API")


class AnalyzeRequest(BaseModel):
    url: str
    prompt: str | None = None


@app.get("/health")
async def health():
    return {"ok": True, "data": {"status": "healthy"}}


@app.post("/api/analyze", dependencies=[Depends(require_api_key)])
async def analyze(request: Request, body: AnalyzeRequest, background_tasks: BackgroundTasks):
    api_key_name = getattr(request.state, "api_key_name", "unknown")

    if not body.url.strip():
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {"code": "INVALID_INPUT", "message": '"url" must be a non-empty string.'},
                "traceId": str(uuid.uuid4()),
            },
        )

    try:
        validate_url(body.url)
    except AppError as e:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {"code": e.code, "message": str(e)},
                "traceId": str(uuid.uuid4()),
            },
        )

    try:
        job_id = create_job(body.url, body.prompt or "")
    except Exception as e:
        trace_id = str(uuid.uuid4())
        _log("ERROR", "job_create_failed", trace_id=trace_id, error=str(e), api_key=api_key_name)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Failed to create analysis job."},
                "traceId": trace_id,
            },
        )

    background_tasks.add_task(process_job, job_id, body.url, body.prompt or "")
    _log("INFO", "job_accepted", job_id=job_id, url=body.url, api_key=api_key_name)

    return JSONResponse(
        status_code=202,
        content={"ok": True, "data": {"jobId": job_id, "status": "pending"}},
    )


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_api_key)])
async def get_job_status(job_id: str):
    job = get_job(job_id)

    if not job:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": "Job not found."},
                "traceId": str(uuid.uuid4()),
            },
        )

    response: dict = {
        "ok": True,
        "data": {
            "jobId": job["id"],
            "status": job["status"],
            "createdAt": job["created_at"],
            "updatedAt": job["updated_at"],
        },
    }

    if job["status"] == "completed" and job.get("result"):
        response["data"]["result"] = job["result"]

    if job["status"] == "failed" and job.get("error"):
        response["data"]["error"] = job["error"]

    return response


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "error": {"code": "NOT_FOUND", "message": "Endpoint not found."},
            "traceId": str(uuid.uuid4()),
        },
    )
