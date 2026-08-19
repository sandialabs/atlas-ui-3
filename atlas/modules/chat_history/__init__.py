"""Chat history persistence module using SQLAlchemy with DuckDB/PostgreSQL."""

from .conversation_repository import ConversationRepository
from .database import get_engine, get_session_factory, init_database
from .models import (
    Base,
    ConversationRecord,
    MessageRecord,
    TagRecord,
    UserPromptRecord,
    UserWorkspaceRecord,
)
from .user_prompt_repository import UserPromptRepository
from .workspace_repository import WorkspaceRepository

__all__ = [
    "get_engine",
    "get_session_factory",
    "init_database",
    "ConversationRepository",
    "UserPromptRepository",
    "WorkspaceRepository",
    "Base",
    "ConversationRecord",
    "MessageRecord",
    "TagRecord",
    "UserPromptRecord",
    "UserWorkspaceRecord",
]
