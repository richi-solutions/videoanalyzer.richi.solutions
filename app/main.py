import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.analyze import process_job
from app.auth import require_api_key
from app.jobs import create_job, get_job

load_dotenv()


def _get_key_from_header(request: Request) -> str:
    return request.headers.get("x-api-key", get_remote_address(request))


limiter = Limiter(key_func=_get_key_from_header)

app = FastAPI(title="Video Analyzer API")
app.state.limiter = limiter


def _rate_limit_error_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "error": {"code": "RATE_LIMITED", "message": f"Too many requests. {exc.detail}"},
            "traceId": str(uuid.uuid4()),
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_error_handler)


class AnalyzeRequest(BaseModel):
    url: str
    prompt: str | None = None


@app.get("/health")
async def health():
    return {"ok": True, "data": {"status": "healthy"}}


@app.post("/api/analyze", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
async def analyze(request: Request, body: AnalyzeRequest, background_tasks: BackgroundTasks):
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
        job_id = create_job(body.url, body.prompt or "")
    except Exception as e:
        trace_id = str(uuid.uuid4())
        print(f"[{trace_id}] Failed to create job: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Failed to create analysis job."},
                "traceId": trace_id,
            },
        )

    # Fire and forget — processing happens in the background
    background_tasks.add_task(process_job, job_id, body.url, body.prompt or "")

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
