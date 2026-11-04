# Development report

> **Draft for the author to edit.** It records what happened during the build, taken from the working session. The sections on
> your own decisions and judgement ("What I decided") are placeholders for you to confirm or rewrite in your own words.

## AI tools used

- **Claude Code** (Claude Sonnet 5.5 in the Claude desktop app) wrote the code, tests, Docker setup, evaluation harness and docs,
  working from the Word build plan.
- **Runtime models** (these are part of the product, not the dev tooling): Claude Sonnet 5.5 (answers, analysis, reports),
  Claude Haiku 5.5 (routing, query rewrite, verification, planning), Gemini 2.5 Flash (fallback provider and evaluation judge),
  local `all-MiniLM-L6-v2` embeddings and an optional local cross-encoder reranker.
- **Langfuse** (EU cloud) for traces of every chat turn.

## What was delegated to the AI

Almost all implementation: FastAPI backend, Alembic migrations, ingestion pipeline, hybrid retrieval, LLM gateway, RAG answering and
verifier, memory, the LangGraph agent, the Next.js frontend, the test suite (75+ tests with fake LLM providers), the evaluation
corpus/dataset/runner, CI and deployment config.

## What I decided  *(author to confirm)*

- Follow the plan's stack and module boundaries (single Postgres with pgvector, LangGraph, Claude primary with a second provider).
- Use a free local embedding model because the OpenAI key had no credit; Gemini free tier for the second provider.
- Backend published on port 5500; Langfuse EU region.
- Which deliverables to prioritise, and when to stop adding features.

## AI-generated bugs and flaws caught

Each was found by running something (tests, a live call, the browser), not by reading the code.

| # | Problem | How it was found | Fix |
|---|---|---|---|
| 1 | `alembic` CLI could not import `app` inside the container | Container exited on first `docker compose up`; unit tests passed because pytest adds the project root to the path | `prepend_sys_path = .` in `alembic.ini` |
| 2 | Default embedding provider was OpenAI with no key, breaking document processing | 11 failing tests after the retrieval change | Default to a provider that works offline; tests pin their own embedder |
| 3 | TXT heading heuristics: an ALL-CAPS heading swallowed its `=====` underline, and numbered list items became headings | Failing unit tests | Handle underlines after heading classification; numbered lines need blank lines on both sides |
| 4 | **Claude Sonnet 5.5 rejects a forced `tool_choice`**, so every Sonnet call failed and silently fell back to Gemini | A live run showed `models: [haiku, gemini]` and a 22% error rate in `llm_calls` | Provider learns per model and switches to `tool_choice: auto`; regression test with a mock transport |
| 5 | Long conversational queries fell below the relevance gate: filler words halved the keyword rank | A follow-up test returned "not found" although the passage was retrieved | Strip question filler from the keyword query |
| 6 | Citation parsing broke when a section label contained `>` inside an XML-style attribute | Fake-provider test parsed an empty source | Sanitise attribute values in `format_sources` |
| 7 | Tracing used Langfuse's ingestion API, which is being shut down on 2026-11-16 | HTTP 410 on the read API and a deprecation notice from the write API | Rewrote tracing on OpenTelemetry (OTLP/JSON) |
| 8 | Test runs could have sent fake traces into the real Langfuse project | Noticed while verifying traces | Test config blanks the Langfuse keys |
| 9 | The `.env` Gemini line was `name = value` with spaces, so it would never be loaded | Reading `.env` structure | Normalised to `GEMINI_API_KEY=` |
| 10 | Eval ingest read a stale cached row and reported a document as unprocessed | Eval crashed on one document | Expire the session before reading status |
| 11 | Retrieval metrics moved between identical runs because ties are broken by random UUIDs | Two runs of the same config disagreed | Repeat each retrieval config three times and report mean (min–max) |
| 12 | The evaluation judge silently fell back to the answerer's own model family when Gemini's free tier rate-limited | Run logs showed circuit-breaker messages; 16 of 28 judgements were by Haiku | Judge gateway can only reach Gemini models, paces calls and caches judgements; Gemini's free daily quota still ran out, so an explicit opt-in flag labels the remaining 10 as same-family (re-run later to upgrade them) |
| 13 | Relevance gate default (0.2) let most unanswerable questions through the retrieval stage | Evaluation threshold sweep | Raised to 0.4 (tuned on the same set; see limitations) |
| 14 | A cross-document synthesis question missed two of three reports with a single `top_k=10` search | Evaluation: partial score; the agent step log showed the passages | Planner guidance for wider `top_k` did **not** fix it. Root cause is vocabulary mismatch ("fixes" vs "actions"); multi-query expansion would, but is not implemented. Documented in the report |

## How it was validated

- 75+ automated tests (pytest, run in Docker against a real Postgres/pgvector): auth, IDOR/isolation (404 for other users'
  rows), upload validation, deduplication, cascade deletes, processing locks, ingestion edge cases, gateway behaviour
  (fallback, retries, circuit breaker, repair), verifier, agent limits and recovery, chat events.
- Live runs against Claude, Gemini and Langfuse after each phase; browser check of the UI.
- A 28-question evaluation with retrieval experiments (`eval/report.md`).

## What I would do next  *(author to edit)*

- A held-out evaluation set and a larger, messier corpus (scanned PDFs, longer documents).
- Aggregation over tables (counts, averages) via a deterministic tool rather than retrieval.
- Streaming tokens from the model instead of replaying a finished answer.
- Per-user token budgets and an answer cache (in the plan, not built).
