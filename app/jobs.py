"""
Firestore job CRUD and stale-job detection for the video analysis pipeline.

Each analysis request is represented as a document in the ``jobs`` Firestore
collection. Status transitions: pending -> processing -> completed | failed.

Jobs stuck in ``processing`` for longer than STALE_JOB_TIMEOUT_SECONDS are
automatically marked as failed when read, compensating for Cloud Run CPU
throttling that can kill background tasks mid-execution.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore

# Jobs stuck in "processing" longer than this are marked as failed.
# Cloud Run may kill background tasks after ~200s (CPU throttling).
STALE_JOB_TIMEOUT_SECONDS = 300


def _get_db() -> firestore.Client:
    """Return a Firestore client for the configured GCP project.

    Uses the ``GCP_PROJECT_ID`` env var when set; otherwise falls back to
    Application Default Credentials (ADC) which resolves the project
    automatically on Cloud Run.

    Returns:
        firestore.Client: Authenticated Firestore client.
    """
    project = os.getenv("GCP_PROJECT_ID")
    if project:
        return firestore.Client(project=project)
    return firestore.Client()


COLLECTION = "jobs"


def create_job(url: str, prompt: str) -> str:
    """Create a new analysis job document in Firestore with ``pending`` status.

    Args:
        url: Public video URL to be analyzed.
        prompt: Analysis instruction; may be an empty string if the caller
            did not supply one (the default prompt is applied in analyze.py).

    Returns:
        str: The new UUID job ID.

    Raises:
        google.cloud.exceptions.GoogleCloudError: If the Firestore write fails.
    """
    db = _get_db()
    job_id = str(uuid.uuid4())
    db.collection(COLLECTION).document(job_id).set({
        "status": "pending",
        "url": url,
        "prompt": prompt,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    })
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    """Update arbitrary fields on a job document, always refreshing ``updated_at``.

    Args:
        job_id: The UUID identifying the Firestore document.
        **fields: Field-value pairs to set (e.g., ``status="completed"``,
            ``result={...}``).

    Raises:
        google.cloud.exceptions.NotFound: If the document does not exist.
        google.cloud.exceptions.GoogleCloudError: On other Firestore errors.
    """
    db = _get_db()
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.collection(COLLECTION).document(job_id).update(fields)


def get_job(job_id: str) -> dict | None:
    """Fetch a job document by ID, with stale-job detection.

    If the job is in ``processing`` status and ``updated_at`` is older than
    STALE_JOB_TIMEOUT_SECONDS, the job is marked as ``failed`` with code
    ``JOB_STALE`` before returning. This compensates for Cloud Run CPU
    throttling that can silently kill background tasks.

    Args:
        job_id: The UUID identifying the Firestore document.

    Returns:
        dict | None: Job data including ``id``, or ``None`` if not found.
    """
    db = _get_db()
    doc = db.collection(COLLECTION).document(job_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = doc.id

    # Detect stale jobs: if "processing" for too long, mark as failed.
    # This catches background tasks killed by Cloud Run CPU throttling.
    if data.get("status") == "processing" and data.get("updated_at"):
        try:
            updated = datetime.fromisoformat(data["updated_at"])
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            if age > STALE_JOB_TIMEOUT_SECONDS:
                stale_error = {
                    "code": "JOB_STALE",
                    "message": f"Job stuck in processing for {int(age)}s. Background task likely killed by runtime.",
                }
                update_job(job_id, status="failed", error=stale_error)
                data["status"] = "failed"
                data["error"] = stale_error
        except (ValueError, TypeError):
            pass

    return data
