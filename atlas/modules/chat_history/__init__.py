"""Chat history persistence module using SQLAlchemy with DuckDB/PostgreSQL."""

from .conversation_repository import ConversationRepository
from .database import PoolSettings, get_engine, get_session_factory, init_database
from .models import (
    Base,
    ConversationRecord,
    MessageRecord,
    PromptLibraryAuditRecord,
    PromptLibraryGrantRecord,
    PromptLibraryPromptRecord,
    PromptLibraryVersionRecord,
    TagRecord,
    UserPromptRecord,
    UserWorkspaceRecord,
)
from .prompt_library_repository import PromptLibraryRepository
from .user_prompt_repository import UserPromptRepository
from .workspace_repository import WorkspaceRepository

__all__ = [
    "PoolSettings",
    "get_engine",
    "get_session_factory",
    "init_database",
    "ConversationRepository",
    "PromptLibraryRepository",
    "UserPromptRepository",
    "WorkspaceRepository",
    "Base",
    "ConversationRecord",
    "MessageRecord",
    "PromptLibraryAuditRecord",
    "PromptLibraryGrantRecord",
    "PromptLibraryPromptRecord",
    "PromptLibraryVersionRecord",
    "TagRecord",
    "UserPromptRecord",
    "UserWorkspaceRecord",
]
