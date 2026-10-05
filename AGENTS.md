# Contributor and agent guidance

## Scope

This repository is a self-hosted London rental-alert application, not a public property scraper or hosted SaaS. Keep changes focused on this project. Do not modify host networking, firewall, VPN, reverse proxy, system services, or unrelated Docker resources as part of ordinary code work.

## Architecture

- `src/rental_bot/sources/` ingests permitted alert emails into `RawListing`; `normalize.py` and `db.py` handle canonicalization, deduplication, and persistence.
- `enrichment.py`, `detail_viewer.py`, `matching.py`, `assessment.py`, and `ai_review.py` enrich and assess stored listings. Mail-provider details must not leak into these layers.
- `cli.py` owns the worker schedule, ingestion, and notifications; `api.py` exposes the private web API; `frontend/` contains the React interface. Human review status is one shared value per listing.
- PostgreSQL is the only persistent service. Docker Compose binds web and database ports to host loopback; do not make them public without adding an explicit access-control design.

## Safety and privacy

- Never commit `.env`, mailbox credentials, API keys, chat IDs, real email content, production data, private URLs, precise personal destinations, or VPS-specific access details. Use synthetic fixtures and example addresses.
- Do not print secrets or full email bodies in tests, logs, patches, or issue reports. Keep secrets in local environment variables or another operator-managed secret store.
- New source adapters must use the common listing model and respect provider terms, robots restrictions, rate limits, authentication, and technical access controls. Do not add CAPTCHA or anti-bot bypasses. Page review stays opt-in per approved domain and fails closed when access is blocked.
- Keep optional paid integrations disabled by default. Never put server-side Google Maps or OpenAI keys in browser assets.
- Preserve current users' data and review decisions; avoid destructive migrations or deleting Docker volumes. Do not deploy to or publish an existing operator's private installation while preparing public documentation.

## Checks

- Python: `python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest`.
- Frontend: `cd frontend && npm ci --legacy-peer-deps && npm test && npm run build`.
- Compose: `docker compose -f docker-compose.yml config --quiet` and confirm port bindings remain loopback-only. Use synthetic data for fresh-clone acceptance.
- Before a public push, inspect the exact staged tree and Git history for secrets, personal identifiers, private hostnames, and generated artifacts. Have the operator confirm repository name, license, and publication target.
