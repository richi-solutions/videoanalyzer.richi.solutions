import os
import tempfile
import time

import google.generativeai as genai
import yt_dlp

DEFAULT_PROMPT = "Describe in detail what happens in this video, including visuals and audio."
DEFAULT_MODEL = "gemini-2.0-flash"

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 24  # 2 minutes total


class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def analyze_video(url: str, prompt: str = DEFAULT_PROMPT) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AppError("MISCONFIGURATION", "GEMINI_API_KEY is not set.")

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    tmp_path = tempfile.mktemp(suffix=".mp4")

    try:
        # Step 1: Download video via yt-dlp (platform-agnostic, native Python)
        _download_video(url, tmp_path)

        # Step 2: Upload to Gemini File API
        genai.configure(api_key=api_key)
        uploaded_file = genai.upload_file(tmp_path, mime_type="video/mp4")

        # Step 3: Poll until file state is ACTIVE
        attempts = 0
        while uploaded_file.state.name == "PROCESSING":
            if attempts >= MAX_POLL_ATTEMPTS:
                raise AppError(
                    "GEMINI_TIMEOUT",
                    "Gemini file processing timed out after 2 minutes.",
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            uploaded_file = genai.get_file(uploaded_file.name)
            attempts += 1

        if uploaded_file.state.name == "FAILED":
            raise AppError("GEMINI_FILE_FAILED", "Gemini file processing failed.")

        # Step 4: Generate content
        model = genai.GenerativeModel(model_name=model_name)
        response = model.generate_content([uploaded_file, prompt])

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
