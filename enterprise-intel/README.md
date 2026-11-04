# Enterprise Intelligence Platform

Multi-user platform where each user uploads documents (PDF, TXT, MD, CSV) and chats with them, with grounded, cited answers and a LangGraph agent for analysis and reports. See `CLAUDE.md` for conventions and the module map.

## Run (Docker)

```bash
cp .env.example .env        # then set JWT_SECRET
docker compose up --build
```

- Frontend: http://localhost:3000
- API + Swagger: http://localhost:8000/docs
- Postgres (pgvector): localhost:5432, user/password `app`

## Tests

```bash
docker compose run --rm backend pytest -q
```

## Status

| Area | State |
|---|---|
| Docker stack, Alembic schema (all tables, HNSW + GIN indexes) | done |
| Auth: register/login/refresh/logout/me, per-user isolation | done |
| Upload: validation (magic bytes), SHA-256 dedupe, background processing, reprocess, delete | done |
| Ingestion: PDF/TXT/MD/CSV extraction, cleaning, heading + table detection, chunking, stats, injection flagging | done |
| Frontend: login, register, documents (drag-drop upload, live status) | done |
| Embeddings, hybrid retrieval, RAG answers + citations | next |
| LLM gateway (Claude + OpenAI fallback), LangGraph agent, memory, verifier | next |
| Dashboard, chat UI, logs, evaluation, CI | next |

## Design notes
- **Tenant isolation:** every query filters by `user_id` in SQL; foreign IDs return 404.
- **Idempotent processing:** an atomic status claim prevents the same document being processed twice.
- **Deterministic ingestion:** no LLM calls until the embedding step.
