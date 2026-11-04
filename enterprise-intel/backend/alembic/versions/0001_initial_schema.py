"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  size_bytes BIGINT,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded',
  error_message TEXT,
  processing_job_id TEXT,
  stats JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  processed_at TIMESTAMPTZ,
  UNIQUE (user_id, content_hash)
);
CREATE INDEX ix_documents_user_id ON documents (user_id);

CREATE TABLE chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  content TEXT NOT NULL,
  heading_path TEXT[],
  page_start INT, page_end INT,
  content_type TEXT,
  token_count INT, word_count INT,
  metadata JSONB,
  flagged_injection BOOLEAN DEFAULT false,
  embedding vector(1536),
  embedding_model TEXT,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunks USING gin (tsv);
CREATE INDEX ON chunks (user_id, document_id);
CREATE INDEX ix_chunks_user_id ON chunks (user_id);

CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT,
  summary TEXT,
  active_document_ids UUID[] DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_conversations_user_id ON conversations (user_id);

CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations JSONB,
  route TEXT,
  status TEXT,
  groundedness_score REAL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE TABLE agent_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,
  step_no INT, tool_name TEXT,
  args JSONB, output_summary JSONB,
  status TEXT, error TEXT,
  latency_ms INT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE llm_calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID, conversation_id UUID, message_id UUID,
  task TEXT, provider TEXT, model TEXT,
  input_tokens INT, output_tokens INT, cost_usd NUMERIC(10,6),
  latency_ms INT, success BOOLEAN, error TEXT,
  fallback_used BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE activity_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID, action TEXT,
  entity_type TEXT, entity_id UUID,
  details JSONB, level TEXT DEFAULT 'info',
  created_at TIMESTAMPTZ DEFAULT now()
);
"""


def upgrade() -> None:
    for statement in SCHEMA.split(";\n"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in ("activity_logs", "llm_calls", "agent_steps", "messages", "conversations", "chunks", "documents", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
