"""Add user_prompts table (custom prompt library, issue #153).

Revision ID: 002
Revises: 001
Create Date: 2026-06-04

No database-level foreign key constraints for DuckDB compatibility.
Referential integrity is enforced in the application/repository layer.
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_email", sa.String(255), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_user_prompts_user_updated", "user_prompts", ["user_email", "updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_prompts_user_updated", table_name="user_prompts")
    op.drop_table("user_prompts")
