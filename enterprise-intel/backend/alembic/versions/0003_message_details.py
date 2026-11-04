"""messages.details (model, tokens, latency, verification issues, conflicts)

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN details JSONB")
    op.execute("CREATE INDEX ix_llm_calls_user_id ON llm_calls (user_id)")
    op.execute("CREATE INDEX ix_activity_logs_user_id ON activity_logs (user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activity_logs_user_id")
    op.execute("DROP INDEX IF EXISTS ix_llm_calls_user_id")
    op.execute("ALTER TABLE messages DROP COLUMN details")
