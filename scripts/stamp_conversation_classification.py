#!/usr/bin/env python3
"""Record a data classification on legacy saved conversations (issue #1042).

Conversations saved before ATLAS recorded a conversation-level data
classification have no record. While compliance levels are enforced such a
conversation fails closed: it stays listed and exportable but cannot be opened
into the chat or continued, because nothing says which level its history
belongs to.

This script is the explicit migration step. An operator who knows what a set
of legacy conversations holds assigns them a level; only conversations with
no record are touched, and a recorded classification is never rewritten. The
level must be one the deployment defines (compliance-levels.json), or
``--unclassified`` to record them as saved with no level.

Usage:
    python scripts/stamp_conversation_classification.py --level UUR --user alice@example.com
    python scripts/stamp_conversation_classification.py --level UUR --id <conversation-id> --id <...>
    python scripts/stamp_conversation_classification.py --unclassified --all-users --dry-run

The database is the one the application uses (CHAT_HISTORY_DB_URL or the
DB_* settings), or ``--db-url``. For DuckDB, stop the application first: it
takes an exclusive lock on the file.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas.core.compliance import get_compliance_manager  # noqa: E402
from atlas.core.user_identity import normalize_user_email  # noqa: E402
from atlas.domain.conversation_classification import CLASSIFICATION_METADATA_KEY  # noqa: E402
from atlas.modules.chat_history.conversation_repository import ConversationRepository  # noqa: E402
from atlas.modules.chat_history.database import get_session_factory, init_database  # noqa: E402
from atlas.modules.chat_history.models import ConversationRecord  # noqa: E402


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--level", help="Defined compliance level to record")
    target.add_argument(
        "--unclassified", action="store_true",
        help="Record the conversations as saved with no compliance level",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--user", help="Only this user's conversations")
    scope.add_argument("--id", action="append", dest="ids", help="A conversation id (repeatable)")
    scope.add_argument("--all-users", action="store_true", help="Every legacy conversation")
    parser.add_argument("--db-url", help="SQLAlchemy URL; defaults to the app setting")
    parser.add_argument("--dry-run", action="store_true", help="Count, change nothing")
    return parser.parse_args(argv)


def _count_legacy(session_factory, user, ids):
    with session_factory() as session:
        query = session.query(ConversationRecord)
        if user:
            query = query.filter(ConversationRecord.user_email == normalize_user_email(user))
        if ids:
            query = query.filter(ConversationRecord.id.in_(ids))
        count = 0
        for conv in query.all():
            try:
                meta = json.loads(conv.metadata_json) if conv.metadata_json else {}
            except json.JSONDecodeError:
                continue
            if isinstance(meta, dict) and CLASSIFICATION_METADATA_KEY not in meta:
                count += 1
        return count


def main(argv=None) -> int:
    args = _parse_args(argv)
    level = None
    if args.level:
        level = get_compliance_manager().validate_compliance_level(args.level, context="stamp script")
        if not level:
            print(f"Not a defined compliance level: {args.level}", file=sys.stderr)
            return 2

    db_url = args.db_url
    if not db_url:
        from atlas.modules.config import config_manager

        db_url = config_manager.app_settings.chat_history_db_url
    init_database(db_url)
    factory = get_session_factory()

    if args.dry_run:
        count = _count_legacy(factory, args.user, args.ids)
        print(f"{count} legacy conversation(s) would be recorded as {level or 'unclassified'}")
        return 0

    stamped = ConversationRepository(factory).stamp_legacy_classification(
        level, user_email=args.user, conversation_ids=args.ids
    )
    print(f"Recorded {level or 'unclassified'} on {stamped} legacy conversation(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
