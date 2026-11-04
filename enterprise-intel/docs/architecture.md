# Architecture

## Components

```mermaid
flowchart LR
  U[Browser<br/>Next.js] -->|HTTPS + JWT, SSE| API[FastAPI backend]
  API --> AUTH[Auth<br/>JWT access + refresh cookie, bcrypt]
  API --> ING[Upload endpoint<br/>validate, hash, store]
  ING -->|202| BG[Background task<br/>process_document]
  BG --> PIPE[Ingestion pipeline<br/>extract, clean, structure, chunk, stats]
  PIPE --> EMB[Embedder<br/>MiniLM local / OpenAI]
  EMB --> DB[(PostgreSQL + pgvector)]
  ING --> FS[(File storage)]
  API --> CHAT[Chat service]
  CHAT --> MEM[Memory manager<br/>summary, recent turns, active docs]
  CHAT --> G[LangGraph agent]
  G --> RET[Hybrid retriever<br/>vector + keyword, RRF, gate]
  RET --> DB
  G --> GW[LLM gateway<br/>Claude primary, Gemini fallback]
  G --> VER[Verifier<br/>citation checks + claim judge]
  GW --> LOG[llm_calls table]
  GW --> LF[Langfuse via OpenTelemetry]
  CHAT --> DB
```

## Upload flow (no AI until the embedding step)

```mermaid
flowchart TD
  A[Upload] --> B{Validate type, size, magic bytes}
  B -- invalid --> X[400]
  B -- ok --> C[SHA-256]
  C --> D{Same hash for this user?}
  D -- yes --> Y[200 duplicate: true]
  D -- no --> E[Store file + documents row: uploaded]
  E --> F[202 returned; background task starts]
  F --> G[Atomic claim: uploaded to processing]
  G --> H[Extract per file type]
  H --> I[Clean: headers/footers, hyphens, whitespace]
  I --> J[Detect headings and tables]
  J --> K[Section-aware chunks with overlap]
  K --> L[Embed contextual text]
  L --> M[Store chunks + vectors + tsvector]
  M --> N[ready]
  H -. exception .-> Z[failed + error message]
```

## Question flow

```mermaid
flowchart TD
  Q[Message] --> M[Load memory]
  M --> R{Router}
  R -- direct --> D[Direct reply]
  R -- rag --> RW[Rewrite follow-up using memory]
  RW --> HS[Hybrid search, user-scoped SQL]
  HS --> GATE{Relevant evidence?}
  GATE -- no --> NF[Not found in your documents, no LLM generation]
  GATE -- yes --> GEN[Answer with citations and conflict detection]
  R -- agent --> PL[Planner picks next tool]
  PL --> T[knowledge_base_search, document_analysis, generate_report]
  T -->|step limit, loop and arg checks| PL
  GEN --> V[Verify: citations, quotes, claim support, one regeneration]
  T --> V
  V --> OUT[Stream route, steps, answer, citations, verification]
```

## Key decisions

| Decision | Why |
|---|---|
| Postgres + pgvector, not a vector DB | One database for users, documents, chunks, vectors and logs; `user_id` filters are plain SQL; transactional consistency; full-text search is built in for the keyword half of hybrid search |
| Hybrid search with Reciprocal Rank Fusion | Embeddings capture meaning, keywords capture exact identifiers (ERR-4471, INC-102); RRF needs no score normalisation |
| LangGraph for the agent | Explicit state, conditional edges, step limits and loop detection are visible and testable |
| Deterministic ingestion | Parsing, cleaning, chunking and counting never call an LLM, so they are cheap, fast and reproducible |
| Gateway with fallback | Every LLM call goes through one place: retries, circuit breaker, provider fallback, structured-output validation and logging |
| Relevance gate before generation | No evidence means no generation, which saves tokens and prevents hallucination |
| Tenant isolation in SQL | Ownership is in the `WHERE` clause (never a Python post-filter); another user's row is a 404; agent tools get `user_id` from server state, never from LLM output |

## Reliability and safety features

- Retries with backoff, a per-provider circuit breaker, fallback provider, one structured-output repair retry.
- Agent: max steps, per-tool and overall timeouts, Pydantic argument validation with repair, loop detection, partial results.
- Prompt injection: ingestion-time flagging, delimited and labelled untrusted sources with extra warnings on flagged chunks,
  output checks for prompt leakage and unsourced links.
- Verification: deterministic citation and quote checks plus an LLM claim check against the sources.
