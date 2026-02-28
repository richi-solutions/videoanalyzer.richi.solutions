import os
import uuid
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore

# Jobs stuck in "processing" longer than this are marked as failed.
# Cloud Run may kill background tasks after ~200s (CPU throttling).
STALE_JOB_TIMEOUT_SECONDS = 300


def _get_db() -> firestore.Client:
    project = os.getenv("GCP_PROJECT_ID")
    if project:
        return firestore.Client(project=project)
    return firestore.Client()


COLLECTION = "jobs"


def create_job(url: str, prompt: str) -> str:
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
    db = _get_db()
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.collection(COLLECTION).document(job_id).update(fields)


def get_job(job_id: str) -> dict | None:
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
