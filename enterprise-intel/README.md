# Enterprise Intelligence Platform

Multi-user platform where each user uploads documents (PDF, TXT, MD, CSV) and chats with them, with grounded, cited answers and a LangGraph agent for analysis and reports. See `CLAUDE.md` for conventions and the module map.

## Run (Docker)

```bash
cp .env.example .env        # then set JWT_SECRET
docker compose up --build
```

- Frontend: http://localhost:3000
- API + Swagger: http://localhost:5500/docs
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
| Embeddings (free local all-MiniLM-L6-v2, 384-d; optional OpenAI; offline hash for tests), hybrid retrieval (vector + keyword, RRF, per-doc cap, relevance gate), `POST /search` | done |
| LLM gateway: Claude (Haiku/Sonnet) primary, Gemini fallback; retries, circuit breaker, structured-output repair, call logging | done |
| RAG answers with citations, relevance gate, conflict detection, verifier (deterministic checks + LLM judge + one regeneration) | done |
| Prompt-injection defence (ingestion flags, delimited sources, output checks) | done |
| Conversation memory (rolling summary, last turns, active documents, query rewrite) | done |
| LangGraph agent: router, search → analysis → report, step limit, arg repair, loop detection, timeouts | done |
| Chat API with SSE events, conversations CRUD, `/stats/*`, `/logs/*`, `/models`; Langfuse tracing (OpenTelemetry) | done |
| Frontend: app shell + auth guard, dashboard (KPIs, usage chart, providers, activity), document detail (outline, keywords, chunk browser), chat (streaming, sources / agent trace / details panels, conflict + not-found cards, document scope + model picker), logs | done |
| Evaluation: 10-document corpus, 28 questions, retrieval experiments, end-to-end run (`backend/eval/`, results in `backend/eval/report.md`) | done |
| Optional local cross-encoder reranker (`RERANK_ENABLED`), off by default (no measurable gain on the eval set) | done |
| GitHub Actions CI (lint, tests, frontend type-check and build) | written and pushed |
| Cloud deployment | **not done by decision: the project runs locally with Docker** |

## Design notes
- **Tenant isolation:** every query filters by `user_id` in SQL; foreign IDs return 404.
- **Idempotent processing:** an atomic status claim prevents the same document being processed twice.
- **Deterministic ingestion:** no LLM calls until the embedding step.

## Documentation
- [Architecture and design decisions](mdfile/architecture.md) · [Database schema](mdfile/schema.md) · [Running locally](mdfile/deployment.md) · [User guide](mdfile/user_guide.md)
- [Evaluation report](backend/eval/report.md) · [Development report (draft)](mdfile/development_report.md) · [CLAUDE.md](CLAUDE.md)

## Retrieval strategy (why hybrid)
Enterprise documents mix natural language, where semantic search wins, with exact identifiers such as error codes, ticket
numbers and incident ids, where keyword search wins. Postgres provides both (pgvector and full-text search) in one query path,
fused with Reciprocal Rank Fusion, so no second system is needed. A rewrite step resolves follow-up questions, and a relevance
gate stops generation when nothing relevant was retrieved. The evaluation compares strategies honestly, including where they do
not differ.
