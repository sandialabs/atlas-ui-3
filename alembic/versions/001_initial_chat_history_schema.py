"""Initial chat history schema.

Revision ID: 001
Create Date: 2026-02-15

No database-level foreign key constraints for DuckDB compatibility.
Referential integrity is enforced in the application/repository layer.
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_email", sa.String(255), nullable=False, index=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_conversations_user_updated", "conversations", ["user_email", "updated_at"]
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("message_type", sa.String(50), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text, nullable=True),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("user_email", sa.String(255), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "user_email", name="uq_tag_name_user"),
    )

    op.create_table(
        "conversation_tags",
        sa.Column("conversation_id", sa.String(36), primary_key=True),
        sa.Column("tag_id", sa.String(36), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("conversation_tags")
    op.drop_table("tags")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
