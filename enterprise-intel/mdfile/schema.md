# Database schema

PostgreSQL 16 with the `pgvector` extension. Migrations are in `backend/alembic/versions/`:

| Migration | Change |
|---|---|
| `0001_initial_schema` | All tables, HNSW (cosine) index on `chunks.embedding`, GIN index on `chunks.tsv`, ownership indexes |
| `0002_embedding_384` | `chunks.embedding` from `vector(1536)` to `vector(384)` for the local MiniLM model (discards old vectors; reprocess documents) |
| `0003_message_details` | `messages.details` (model, tokens, latency, verification issues, conflicts) and indexes on `llm_calls`/`activity_logs` |

```mermaid
erDiagram
  users ||--o{ documents : owns
  users ||--o{ chunks : owns
  users ||--o{ conversations : owns
  documents ||--o{ chunks : "split into"
  conversations ||--o{ messages : contains
  messages ||--o{ agent_steps : "tool steps"
  users {
    uuid id PK
    text email UK
    text password_hash
    text role
  }
  documents {
    uuid id PK
    uuid user_id FK
    text filename
    text file_type
    text content_hash "unique with user_id"
    text status "uploaded, processing, ready, failed"
    jsonb stats
  }
  chunks {
    uuid id PK
    uuid document_id FK
    uuid user_id FK
    int chunk_index
    text content
    text_array heading_path
    int page_start
    text content_type "text, table, csv_rows, csv_schema"
    bool flagged_injection
    vector384 embedding
    text embedding_model
    tsvector tsv "generated from content"
  }
  conversations {
    uuid id PK
    uuid user_id FK
    text summary
    uuid_array active_document_ids
  }
  messages {
    uuid id PK
    uuid conversation_id FK
    text role
    jsonb citations
    text route "direct, rag, agent"
    text status "answered, insufficient_evidence, conflicting_sources, error"
    real groundedness_score
    jsonb details
  }
  agent_steps {
    uuid id PK
    uuid message_id FK
    int step_no
    text tool_name
    jsonb args
    jsonb output_summary
    text status
    int latency_ms
  }
```

`llm_calls` (user, conversation, message, task, provider, model, tokens, estimated cost, latency, success, error,
`fallback_used`) and `activity_logs` (user, action, entity, details, level) are append-only logs with no foreign keys, so
history survives deletes.

## Rules

- Every table holding user data has `user_id` with an index, and every query filters on it in SQL.
- `UNIQUE (user_id, content_hash)` makes duplicate detection per user and race-safe.
- `chunks.tsv` is `GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`, so it is not a column in the ORM model.
- Embeddings from different models are never compared: search filters on `chunks.embedding_model`.
- Deleting a user, document or conversation cascades to its chunks, messages and agent steps.
