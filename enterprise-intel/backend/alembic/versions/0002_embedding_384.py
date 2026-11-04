"""embeddings: vector(1536) -> vector(384) (local MiniLM model)

Existing embeddings are discarded; reprocess documents to regenerate them.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _resize(dim: int) -> None:
    op.execute("DROP INDEX IF EXISTS chunks_embedding_idx")
    op.execute("UPDATE chunks SET embedding = NULL, embedding_model = NULL")
    op.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({dim})")
    op.execute("CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops)")


def upgrade() -> None:
    _resize(384)


def downgrade() -> None:
    _resize(1536)
