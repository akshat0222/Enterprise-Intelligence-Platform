# Enterprise Intelligence Platform — project conventions

Multi-user RAG + LangGraph agent platform. Build plan: "Enterprise Intelligence Platform — Build Plan.docx".

## Commands (everything runs in Docker)
- `docker compose up --build` — db (pgvector), backend :8000 (Swagger at /docs), frontend :3000
- `docker compose run --rm backend pytest -q` — tests (use a separate `enterprise_intel_test` DB, created automatically)
- `docker compose run --rm backend ruff check .` — lint
- `docker compose run --rm backend alembic upgrade head` — migrations (backend runs this on start)

## Rules (non-negotiable)
- **Always filter by `user_id`.** Ownership goes inside the SQL `WHERE`, never as a Python post-filter. Another user's row returns 404, not 403.
- Never take `user_id` from client input or LLM output; it comes from the JWT / server-side graph state.
- Deterministic before generative: everything in `app/ingestion/` is plain Python, no LLM calls.
- SQL only via the ORM or parameterised queries. Secrets only in env vars (`.env` is git-ignored; edit `.env.example`).
- Error responses use `{"error": {"code", "message"}}` and never include stack traces.

## Module map (backend/app)
- `api/` routers (auth, documents) · `auth/` JWT + bcrypt + `get_current_user` · `db/` models + session
- `ingestion/` validators → extractors/{pdf,txt,markdown,csv_file} → cleaner → structure → tables → chunker → stats → pipeline
- `rag/injection.py` prompt-injection heuristics (flags chunks at ingestion)
- Not built yet (plan §6–§10, §14): embeddings, hybrid retrieval, LLM gateway, LangGraph agent, memory, verifier, eval

## Conventions
- All extractors return `Block` objects (`ingestion/blocks.py`); adding a file type = one new extractor.
- Document status machine: `uploaded → processing → ready|failed`. `process_document` claims a document with an atomic `UPDATE ... WHERE status='uploaded'`, so it never runs twice concurrently.
- Schema changes go through Alembic migrations only.
