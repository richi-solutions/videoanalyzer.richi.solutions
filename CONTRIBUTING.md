# Contributing

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/<short-description>` | `feature/webhook-callback` |
| Bug fix | `fix/<short-description>` | `fix/stale-job-detection` |
| Chore | `chore/<short-description>` | `chore/upgrade-yt-dlp` |
| Documentation | `docs/<short-description>` | `docs/api-endpoints` |
| Refactor | `refactor/<short-description>` | `refactor/extract-gemini-client` |

Never commit directly to `main`.

## Commit Messages

Conventional Commits format. A body is **mandatory** for `feat`, `fix`, `refactor`, and `test` commits.

```
feat: add webhook callback on job completion

Send a POST request to a caller-supplied callback URL when a job
transitions to completed or failed. This removes the need for polling
in integrations that can receive webhooks.

fix: prevent stale cache entries surviving key revocation

Reduce key cache TTL from 5 minutes to 60 seconds to limit the window
in which a revoked key remains admitted after revocation.

chore: upgrade yt-dlp to 2024.12.0
docs: add Firestore schema to architecture overview
```

- Subject: max 72 characters, imperative mood, no trailing period
- Body: blank line after subject, then explain what changed and why
- Scope: optional, e.g. `feat(auth): ...`

## Pull Request Process

1. Create a feature branch from `main`.
2. Make changes, commit with conventional commits.
3. Open a PR against `main` with a clear description of what changed and why.
4. Ensure the Docker image builds locally before requesting review: `docker build .`
5. At least one approval required before merging.

## Code Style

- Python: follow PEP 8; use type hints on all function signatures.
- All code comments and docstrings in English only.
- No secrets in code — use environment variables only.
- All responses use the error envelope standard: `{ ok, data? }` or `{ ok, error, traceId }`.

## Testing

There is currently no automated test suite. When adding tests:

- Place unit tests in `tests/` at the project root.
- Use `pytest` as the test runner.
- Mock external calls (yt-dlp, Gemini API, Firestore) — do not make real network calls in tests.

## Environment Setup

See [README.md — Getting Started](README.md#getting-started) for prerequisites and local run instructions.
