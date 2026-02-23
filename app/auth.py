import os
from fastapi import Header, HTTPException


async def require_api_key(x_api_key: str = Header(...)) -> None:
    service_api_key = os.getenv("SERVICE_API_KEY")

    if not service_api_key:
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": {"code": "MISCONFIGURATION", "message": "Service API key not configured."},
            },
        )

    if x_api_key != service_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": {"code": "UNAUTHORIZED", "message": "Missing or invalid X-API-Key header."},
            },
        )
