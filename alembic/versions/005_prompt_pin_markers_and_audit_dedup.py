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

    # Decode audit JSON in Python for portable handling of damaged JSON.
    named = set()
    for (details,) in conn.execute(
        sa.text("SELECT details_json FROM prompt_library_audit_events WHERE event_type = 'use'")
    ):
        try:
            value = json.loads(details) if details else None
        except (TypeError, ValueError):
            continue
        conversation_id = value.get("conversation_id") if isinstance(value, dict) else None
        if isinstance(conversation_id, str):
            named.add(conversation_id)

    # Stage the distinct audit references in batches instead of issuing
    # one conversation lookup per reference.
    staging = sa.Table(
        "_migration_005_pin_refs",
        sa.MetaData(),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        prefixes=["TEMPORARY"],
    )
    staging.create(conn)

    ids = sorted(named)
    for offset in range(0, len(ids), 500):
        conn.execute(
            staging.insert(),
            [{"conversation_id": cid} for cid in ids[offset:offset + 500]],
        )

    # One set-based insert: preserve conservative metadata matching and
    # ignore audit references to conversations that no longer exist.
    conn.execute(
        sa.text(
            "INSERT INTO conversation_prompt_pin_markers "
            "(conversation_id, user_email, first_pinned_at) "
            "SELECT c.id, c.user_email, :at FROM conversations c "
            "LEFT JOIN _migration_005_pin_refs r ON r.conversation_id = c.id "
            "WHERE c.metadata_json LIKE '%prompt_library%' "
            "OR r.conversation_id IS NOT NULL"
        ),
        {"at": now},
    )
    staging.drop(conn)


def downgrade() -> None:
    op.drop_index("ix_prompt_library_audit_prompt_type_created", table_name="prompt_library_audit_events")
    op.drop_index("uq_prompt_library_audit_dedup", table_name="prompt_library_audit_events")
    with op.batch_alter_table("prompt_library_audit_events") as batch:
        batch.drop_column("dedup_key")
        batch.drop_column("last_occurred_at")
        batch.drop_column("occurrences")
    op.drop_table("conversation_prompt_pin_markers")
