"""Pin markers for legacy compatibility; deduplicated audit events (issue #1169).

Revision ID: 005
Revises: 004
Create Date: 2026-11-23

Additive only.

``conversation_prompt_pin_markers``
    One row per conversation that has, or had, a team-prompt pin. When a
    conversation's metadata cannot be read, a missing marker proves it was
    never pinned, so it behaves exactly as it did before the library existed;
    a marker keeps it fail closed until its owner recovers it. Backfilled
    conservatively: every conversation whose stored metadata mentions the
    library at all (readable or not), and every conversation named by a
    library ``use`` audit event.

``prompt_library_audit_events``
    ``occurrences``, ``last_occurred_at`` and a unique, nullable ``dedup_key``:
    repeated ``use_denied`` events by one actor on one prompt within a time
    window collapse into one row (counted), so they cannot crowd out sharing,
    publishing and management events. Existing rows keep ``occurrences = 1``
    and no key. An index on (prompt_id, event_type, created_at) serves the
    filtered, paged audit view.
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_prompt_pin_markers",
        sa.Column("conversation_id", sa.String(36), primary_key=True),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("first_pinned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "prompt_library_audit_events",
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "prompt_library_audit_events",
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "prompt_library_audit_events",
        sa.Column("dedup_key", sa.String(160), nullable=True),
    )
    op.create_index(
        "uq_prompt_library_audit_dedup", "prompt_library_audit_events", ["dedup_key"], unique=True
    )
    op.create_index(
        "ix_prompt_library_audit_prompt_type_created",
        "prompt_library_audit_events",
        ["prompt_id", "event_type", "created_at"],
    )
    _backfill_markers()


def _backfill_markers() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    owners = {}
    # Any mention of the library in stored metadata, readable or not: a blob
    # that cannot be parsed may still have held a pin, so it is marked.
    for conversation_id, user_email in conn.execute(
        sa.text("SELECT id, user_email FROM conversations WHERE metadata_json LIKE '%prompt_library%'")
    ):
        owners[conversation_id] = user_email
    # Conversations a library "use" event names (the event outlives a pin
    # that was later cleared, and survives metadata damage).
    named = set()
    for (details,) in conn.execute(
        sa.text("SELECT details_json FROM prompt_library_audit_events WHERE event_type = 'use'")
    ):
        try:
            value = json.loads(details) if details else None
        except (TypeError, ValueError):
            continue
        conversation_id = value.get("conversation_id") if isinstance(value, dict) else None
        if isinstance(conversation_id, str) and conversation_id not in owners:
            named.add(conversation_id)
    for conversation_id in sorted(named):
        row = conn.execute(
            sa.text("SELECT user_email FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).first()
        if row is not None:
            owners[conversation_id] = row[0]
    for conversation_id, user_email in sorted(owners.items()):
        conn.execute(
            sa.text(
                "INSERT INTO conversation_prompt_pin_markers (conversation_id, user_email, first_pinned_at) "
                "VALUES (:id, :user, :at)"
            ),
            {"id": conversation_id, "user": user_email, "at": now},
        )


def downgrade() -> None:
    op.drop_index("ix_prompt_library_audit_prompt_type_created", table_name="prompt_library_audit_events")
    op.drop_index("uq_prompt_library_audit_dedup", table_name="prompt_library_audit_events")
    with op.batch_alter_table("prompt_library_audit_events") as batch:
        batch.drop_column("dedup_key")
        batch.drop_column("last_occurred_at")
        batch.drop_column("occurrences")
    op.drop_table("conversation_prompt_pin_markers")
