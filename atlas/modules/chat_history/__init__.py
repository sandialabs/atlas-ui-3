"""Chat history persistence module using SQLAlchemy with DuckDB/PostgreSQL."""

from .conversation_repository import ConversationRepository
from .database import get_engine, get_session_factory, init_database
from .models import Base, ConversationRecord, MessageRecord, TagRecord

__all__ = [
    "get_engine",
    "get_session_factory",
    "init_database",
    "ConversationRepository",
    "Base",
    "ConversationRecord",
    "MessageRecord",
    "TagRecord",
]
