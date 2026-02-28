import asyncio
import json
import os
import re
import sys
import tempfile
import time

from google import genai
import yt_dlp

from app.jobs import update_job

DEFAULT_PROMPT = "Describe in detail what happens in this video, including visuals and audio."
DEFAULT_MODEL = "gemini-2.0-flash"

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 24  # 2 minutes total
MAX_VIDEO_DURATION_SECONDS = 600  # 10 minutes

_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def _log(level: str, event: str, **data):
    """Structured JSON log to stdout for Cloud Run log aggregation."""
    entry = {"severity": level, "event": event, "ts": time.time(), **data}
    print(json.dumps(entry), flush=True, file=sys.stdout)


class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_url(url: str) -> None:
    """Validate URL format before processing."""
    if not _URL_PATTERN.match(url):
        raise AppError("INVALID_INPUT", "URL must start with http:// or https://.")


def _check_video_duration(url: str) -> float | None:
    """Extract video duration via yt-dlp metadata (no download). Returns seconds or None."""
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("duration") if info else None
    except Exception:
        # If metadata extraction fails, let the actual download attempt handle it
        return None


async def process_job(job_id: str, url: str, prompt: str) -> None:
    """Background task: download video, analyze with Gemini, update Firestore."""
    _log("INFO", "job_started", job_id=job_id, url=url)
    try:
        update_job(job_id, status="processing")
        _log("INFO", "job_status_updated", job_id=job_id, status="processing")

        result = await _analyze_video(job_id, url, prompt or DEFAULT_PROMPT)

        update_job(job_id, status="completed", result=result)
        _log("INFO", "job_completed", job_id=job_id, model=result.get("model"))
    except AppError as e:
        _log("ERROR", "job_failed_app_error", job_id=job_id, code=e.code, message=str(e))
        try:
            update_job(job_id, status="failed", error={"code": e.code, "message": str(e)})
        except Exception as fe:
            _log("ERROR", "firestore_update_failed", job_id=job_id, message=str(fe))
    except Exception as e:
        _log("ERROR", "job_failed_unexpected", job_id=job_id, error_type=type(e).__name__, message=str(e))
        try:
            update_job(job_id, status="failed", error={"code": "INTERNAL_ERROR", "message": str(e)})
        except Exception as fe:
            _log("ERROR", "firestore_update_failed", job_id=job_id, message=str(fe))


async def _analyze_video(job_id: str, url: str, prompt: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AppError("MISCONFIGURATION", "GEMINI_API_KEY is not set.")

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    tmp_path = tempfile.mktemp(suffix=".mp4")

    try:
        # Step 0: Validate URL and check video duration (no download)
        validate_url(url)
        duration = await asyncio.to_thread(_check_video_duration, url)
        if duration is not None:
            _log("INFO", "step_metadata_done", job_id=job_id, duration_s=round(duration, 1))
            if duration > MAX_VIDEO_DURATION_SECONDS:
                raise AppError(
                    "VIDEO_TOO_LONG",
                    f"Video duration ({int(duration)}s) exceeds limit ({MAX_VIDEO_DURATION_SECONDS}s).",
                )

        # Step 1: Download video via yt-dlp (blocking -> offloaded to thread)
        _log("INFO", "step_download_start", job_id=job_id, url=url)
        t0 = time.time()
        await asyncio.to_thread(_download_video, url, tmp_path)
        file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
        _log("INFO", "step_download_done", job_id=job_id, duration_s=round(time.time() - t0, 1), file_size_mb=round(file_size / 1048576, 1))

        # Step 2: Upload to Gemini File API (blocking -> offloaded to thread)
        _log("INFO", "step_upload_start", job_id=job_id)
        t0 = time.time()
        client = genai.Client(api_key=api_key)
        uploaded_file = await asyncio.to_thread(client.files.upload, file=tmp_path)
        _log("INFO", "step_upload_done", job_id=job_id, duration_s=round(time.time() - t0, 1), file_name=uploaded_file.name, state=uploaded_file.state.name)

        # Step 3: Poll until file state is ACTIVE (non-blocking sleep)
        attempts = 0
        while uploaded_file.state.name == "PROCESSING":
            if attempts >= MAX_POLL_ATTEMPTS:
                raise AppError(
                    "GEMINI_TIMEOUT",
                    "Gemini file processing timed out after 2 minutes.",
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            uploaded_file = await asyncio.to_thread(
                client.files.get, name=uploaded_file.name
            )
            attempts += 1
            _log("INFO", "step_gemini_poll", job_id=job_id, attempt=attempts, state=uploaded_file.state.name)

        if uploaded_file.state.name == "FAILED":
            raise AppError("GEMINI_FILE_FAILED", "Gemini file processing failed.")

        # Step 4: Generate content (blocking -> offloaded to thread)
        _log("INFO", "step_generate_start", job_id=job_id, model=model_name)
        t0 = time.time()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model_name,
            contents=[uploaded_file, prompt],
        )
        _log("INFO", "step_generate_done", job_id=job_id, duration_s=round(time.time() - t0, 1), response_len=len(response.text))

        return {"analysis": response.text, "model": model_name}

    finally:
        # Always clean up temp file
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _download_video(url: str, output_path: str) -> None:
    ydl_opts = {
        "outtmpl": output_path,
        # Prefer mp4+m4a for clean ffmpeg merge; fallback to best available
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": 500 * 1024 * 1024,  # 500 MB
        # Helps bypass bot detection on TikTok, Instagram, etc.
        "http_headers": {
            "Referer": "https://www.youtube.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        raise AppError("DOWNLOAD_FAILED", f"Failed to download video: {e}") from e
    except Exception as e:
        raise AppError("DOWNLOAD_FAILED", f"Unexpected download error: {e}") from e
