# Enterprise Intelligence Platform — project conventions

Multi-user RAG + LangGraph agent platform.

## Commands (everything runs in Docker)
- `docker compose up --build` — db (pgvector), backend :5500 (Swagger at /docs), frontend :3000
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
- `retrieval/` embedder (`EMBEDDING_PROVIDER=local|openai|hash`, 384-d; never mix models in one search) + `hybrid.py` (RRF, user-scoped SQL, relevance gate)
- `llm/` gateway (task routes in `default_routes`), providers `anthropic.py` / `gemini.py` — add a provider = one file + a route line
- `rag/` answer, verifier, injection, sources · `memory/manager.py` · `prompts/` (versioned) · `observability.py` (Langfuse via OTLP)
- `agent/` graph (LangGraph), registry, tools/ — add a tool = one `@register_tool` function in `agent/tools/`
- `chat/service.py` runs one turn; `api/conversations.py` streams it as SSE
- Frontend (`frontend/`): `app/(app)/*` pages behind `components/AppShell.tsx` (client-side auth guard); `lib/api.ts` has the API client, types and the SSE reader (`streamChat`). Production build check: `docker compose exec -e NEXT_DIST_DIR=.next-build frontend npx next build`
- `backend/eval/`: corpus generator, `dataset.jsonl`, `run.py` (retrieval + end-to-end), `report.py` → `report.md`. Judge model must differ from the answerer's family; `run.py` refuses to fall back.
- CI: `.github/workflows/ci.yml` (repo root). The project runs locally with Docker only (see `docs/deployment.md`).

## Gotchas
- Gemini's free tier has a small daily quota per model; the eval judge defaults to `gemini-3-flash-preview` (`EVAL_JUDGE_MODEL`).
- `MIN_SIMILARITY` (relevance gate) is tuned for the MiniLM embedder; tests set 0.2 for the hashing embedder.
- Claude Sonnet 5.5 rejects forced `tool_choice`; `ClaudeProvider` learns this per model and uses `auto`.
- Tests use fake providers (`tests/fakes.py`) and blank Langfuse keys; never point tests at real keys.
- Langfuse's old ingestion/read APIs are removed on 2026-11-16; tracing uses OTLP and reads use `/api/public/v2/observations`.

## Conventions
- All extractors return `Block` objects (`ingestion/blocks.py`); adding a file type = one new extractor.
- Document status machine: `uploaded → processing → ready|failed`. `process_document` claims a document with an atomic `UPDATE ... WHERE status='uploaded'`, so it never runs twice concurrently.
- Schema changes go through Alembic migrations only.
