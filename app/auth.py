"""
API key authentication and per-key rate limiting middleware.

Supports two authentication modes:
- Multi-key (Firestore): Keys stored as SHA-256 hashes in the ``api_keys``
  collection with individual ``rate_limit`` and ``active`` fields.
- Legacy (env var): Single ``SERVICE_API_KEY`` env var for backward
  compatibility with the TypeScript prototype.

Keys are cached in memory for KEY_CACHE_TTL seconds to minimize Firestore
reads. Rate limiting uses a per-key sliding window over the last 60 seconds.
"""

import hashlib
import os
import time
from collections import defaultdict
from typing import Any

from fastapi import Header, HTTPException, Request

from app.jobs import _get_db


# --- In-memory cache for Firestore key lookups ---
_key_cache: dict[str, dict[str, Any]] = {}
_KEY_CACHE_TTL = 300  # 5 minutes

# --- In-memory sliding window rate limiter ---
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key for Firestore lookup."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _lookup_api_key(raw_key: str) -> dict[str, Any] | None:
    """Look up an API key in Firestore, with in-memory caching."""
    key_hash = _hash_key(raw_key)

    cached = _key_cache.get(key_hash)
    if cached and time.time() - cached["_cached_at"] < _KEY_CACHE_TTL:
        return cached

    try:
        db = _get_db()
        docs = (
            db.collection("api_keys")
            .where("key_hash", "==", key_hash)
            .where("active", "==", True)
            .limit(1)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            data["_cached_at"] = time.time()
            _key_cache[key_hash] = data
            return data
    except Exception:
        pass

    return None


def _check_rate_limit(key_id: str, max_per_minute: int) -> bool:
    """Sliding window rate limiter. Returns True if request is allowed."""
    now = time.time()
    window_start = now - 60

    _rate_buckets[key_id] = [t for t in _rate_buckets[key_id] if t > window_start]

    if len(_rate_buckets[key_id]) >= max_per_minute:
        return False

    _rate_buckets[key_id].append(now)
    return True


async def require_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    """FastAPI dependency that validates the X-API-Key header and enforces rate limits.

    First checks ``SERVICE_API_KEY`` env var for backward-compatible single-key
    auth. If that does not match, looks up the key in the Firestore ``api_keys``
    collection (using a 5-minute in-memory cache).

    Attaches ``request.state.api_key_name`` and ``request.state.rate_limit``
    for use in downstream route handlers and logging.

    Args:
        request: The incoming FastAPI request object.
        x_api_key: Value from the ``X-API-Key`` header (injected by FastAPI).

    Raises:
        HTTPException 401: Key not found or inactive.
        HTTPException 429: Per-key rate limit exceeded.
    """
    key_name: str
    rate_limit: int

    # Fallback: single SERVICE_API_KEY env var (backwards-compatible)
    service_api_key = os.getenv("SERVICE_API_KEY")
    if service_api_key and x_api_key == service_api_key:
        key_name = "legacy"
        rate_limit = 60
    else:
        # Multi-key mode: look up in Firestore
        key_data = _lookup_api_key(x_api_key)
        if not key_data:
            raise HTTPException(
                status_code=401,
                detail={
                    "ok": False,
                    "error": {"code": "UNAUTHORIZED", "message": "Missing or invalid X-API-Key header."},
                },
            )
        key_name = key_data.get("name", "unknown")
        rate_limit = key_data.get("rate_limit", 60)

    # Per-key rate limiting
    if not _check_rate_limit(key_name, rate_limit):
        raise HTTPException(
            status_code=429,
            detail={
                "ok": False,
                "error": {
                    "code": "RATE_LIMITED",
                    "message": f"Rate limit exceeded ({rate_limit} requests/minute).",
                },
            },
        )

    # Store key info on request for downstream logging
    request.state.api_key_name = key_name
    request.state.rate_limit = rate_limit
