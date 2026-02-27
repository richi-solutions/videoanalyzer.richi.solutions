import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.analyze import AppError, analyze_video
from app.auth import require_api_key

load_dotenv()


def _get_key_from_header(request: Request) -> str:
    return request.headers.get("x-api-key", get_remote_address(request))


limiter = Limiter(key_func=_get_key_from_header)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Video Analyzer API", lifespan=lifespan)
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
async def analyze(request: Request, body: AnalyzeRequest):
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
        result = await analyze_video(body.url, body.prompt or "")
        return {"ok": True, "data": result}
    except AppError as e:
        status_map = {
            "INVALID_INPUT": 400,
            "UNAUTHORIZED": 401,
            "DOWNLOAD_FAILED": 422,
            "GEMINI_TIMEOUT": 504,
            "GEMINI_FILE_FAILED": 502,
            "MISCONFIGURATION": 500,
        }
        status = status_map.get(e.code, 500)
        trace_id = str(uuid.uuid4())
        print(f"[{trace_id}] AppError: {e.code} — {e}")
        return JSONResponse(
            status_code=status,
            content={
                "ok": False,
                "error": {"code": e.code, "message": str(e)},
                "traceId": trace_id,
            },
        )
    except Exception as e:
        trace_id = str(uuid.uuid4())
        print(f"[{trace_id}] Unhandled error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
                "traceId": trace_id,
            },
        )


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
