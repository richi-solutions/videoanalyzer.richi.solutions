"""CLI tool for managing API keys in Firestore.

Usage:
    python -m app.manage_keys create --name hookr-production --rate-limit 100
    python -m app.manage_keys list
    python -m app.manage_keys revoke --name hookr-production
"""

import argparse
import hashlib
import secrets
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.jobs import _get_db

load_dotenv()


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_key(name: str, rate_limit: int) -> None:
    db = _get_db()

    # Check for duplicate name
    existing = (
        db.collection("api_keys")
        .where("name", "==", name)
        .where("active", "==", True)
        .limit(1)
        .stream()
    )
    if any(True for _ in existing):
        print(f"Error: Active key with name '{name}' already exists.")
        sys.exit(1)

    raw_key = f"va_{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)

    db.collection("api_keys").add({
        "key_hash": key_hash,
        "name": name,
        "rate_limit": rate_limit,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    print(f"API key created for '{name}':")
    print(f"  Key: {raw_key}")
    print(f"  Rate limit: {rate_limit} req/min")
    print()
    print("Store this key securely — it cannot be retrieved again.")


def list_keys() -> None:
    db = _get_db()
    docs = db.collection("api_keys").stream()

    keys = []
    for doc in docs:
        data = doc.to_dict()
        keys.append(data)

    if not keys:
        print("No API keys found.")
        return

    print(f"{'Name':<30} {'Rate Limit':<12} {'Active':<8} {'Created'}")
    print("-" * 80)
    for k in keys:
        print(f"{k.get('name', '?'):<30} {k.get('rate_limit', '?'):<12} {k.get('active', '?'):<8} {k.get('created_at', '?')}")


def revoke_key(name: str) -> None:
    db = _get_db()
    docs = (
        db.collection("api_keys")
        .where("name", "==", name)
        .where("active", "==", True)
        .stream()
    )

    revoked = 0
    for doc in docs:
        doc.reference.update({"active": False})
        revoked += 1

    if revoked == 0:
        print(f"No active key found with name '{name}'.")
        sys.exit(1)
    else:
        print(f"Revoked {revoked} key(s) for '{name}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage VideoAnalyzer API keys")
    sub = parser.add_subparsers(dest="command", required=True)

    create_p = sub.add_parser("create", help="Create a new API key")
    create_p.add_argument("--name", required=True, help="Key name (e.g., hookr-production)")
    create_p.add_argument("--rate-limit", type=int, default=60, help="Requests per minute (default: 60)")

    sub.add_parser("list", help="List all API keys")

    revoke_p = sub.add_parser("revoke", help="Revoke an API key by name")
    revoke_p.add_argument("--name", required=True, help="Key name to revoke")

    args = parser.parse_args()

    if args.command == "create":
        create_key(args.name, args.rate_limit)
    elif args.command == "list":
        list_keys()
    elif args.command == "revoke":
        revoke_key(args.name)


if __name__ == "__main__":
    main()
