"""Add user_workspaces table (workspace switcher).

Revision ID: 003
Revises: 002
Create Date: 2026-08-17

A workspace is a named bundle of prompt + RAG source + MCP tool selections so a
user can switch chat context (work/home/project) in one click.

No database-level foreign key constraints for DuckDB compatibility.
Referential integrity is enforced in the application/repository layer.
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_email", sa.String(255), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("config_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_user_workspaces_user_updated",
        "user_workspaces",
        ["user_email", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_workspaces_user_updated", table_name="user_workspaces")
    op.drop_table("user_workspaces")
