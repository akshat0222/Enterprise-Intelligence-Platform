"""keyword index covers the section heading (weighted above the body)

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # array_to_string is only STABLE, which a generated column rejects; the inputs here are plain text, so IMMUTABLE is safe.
    op.execute("""
CREATE FUNCTION chunk_tsv(heading text[], body text) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT setweight(to_tsvector('english', coalesce(array_to_string(heading, ' '), '')), 'A')
         || to_tsvector('english', coalesce(body, ''))
$$""")
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (chunk_tsv(heading_path, content)) STORED")
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("ALTER TABLE chunks DROP COLUMN tsv")
    op.execute("ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED")
    op.execute("CREATE INDEX ON chunks USING gin (tsv)")
    op.execute("DROP FUNCTION chunk_tsv(text[], text)")
