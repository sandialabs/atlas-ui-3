"""Add the team-shared prompt library tables (issue #1169).

Revision ID: 004
Revises: 003
Create Date: 2026-11-09

Purely additive: four new tables, nothing existing is altered or dropped, so
``user_prompts`` (issue #153) and every other record are preserved untouched.
Private custom prompts keep working through /api/user-prompts; a user copies
one into the library explicitly ("Save to library").

No database-level foreign key constraints for DuckDB compatibility.
Referential integrity is enforced in the repository layer. The unique
constraint on (prompt_id, version_number) is what keeps two concurrent
publications from producing the same version number, and the one on
(prompt_id, grantee_type, grantee) keeps one grant per grantee. The
draft_revision and sharing_revision counters back optimistic concurrency for
draft edits/publication and sharing changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_library_prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_email", sa.String(255), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("draft_content", sa.Text, nullable=False),
        sa.Column("draft_revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("sharing_revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latest_version_id", sa.String(36), nullable=True),
        sa.Column("latest_version_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("forked_from_prompt_id", sa.String(36), nullable=True),
        sa.Column("forked_from_version_id", sa.String(36), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_prompt_library_prompts_owner_updated",
        "prompt_library_prompts",
        ["owner_email", "updated_at"],
    )

    op.create_table(
        "prompt_library_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_id", sa.String(36), nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("change_description", sa.String(1000), nullable=True),
        sa.Column("author_email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prompt_id", "version_number", name="uq_prompt_library_version_number"),
    )

    op.create_table(
        "prompt_library_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_id", sa.String(36), nullable=False, index=True),
        sa.Column("grantee_type", sa.String(10), nullable=False),
        sa.Column("grantee", sa.String(255), nullable=False),
        sa.Column("permission", sa.String(10), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prompt_id", "grantee_type", "grantee", name="uq_prompt_library_grant"),
    )
    op.create_index(
        "ix_prompt_library_grants_grantee",
        "prompt_library_grants",
        ["grantee_type", "grantee"],
    )

    op.create_table(
        "prompt_library_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_id", sa.String(36), nullable=False, index=True),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_email", sa.String(255), nullable=False),
        sa.Column("details_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_prompt_library_audit_prompt_created",
        "prompt_library_audit_events",
        ["prompt_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_library_audit_prompt_created", table_name="prompt_library_audit_events")
    op.drop_table("prompt_library_audit_events")
    op.drop_index("ix_prompt_library_grants_grantee", table_name="prompt_library_grants")
    op.drop_table("prompt_library_grants")
    op.drop_table("prompt_library_versions")
    op.drop_index("ix_prompt_library_prompts_owner_updated", table_name="prompt_library_prompts")
    op.drop_table("prompt_library_prompts")
