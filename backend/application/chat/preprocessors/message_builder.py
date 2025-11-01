"""Message builder - constructs messages with history and files manifest."""

import logging
from typing import List, Dict, Any, Optional

from domain.sessions.models import Session
from ..utilities import file_utils

logger = logging.getLogger(__name__)


def build_session_context(session: Session) -> Dict[str, Any]:
    """
    Build session context dictionary from session.
    
    Args:
        session: Chat session
        
    Returns:
        Session context dictionary
    """
    return {
        "session_id": session.id,
        "user_email": session.user_email,
        "files": session.context.get("files", {}),
        **session.context
    }


class MessageBuilder:
    """
    Service that builds complete message arrays for LLM calls.
    
    Combines conversation history with files manifest and other context.
    """

    async def build_messages(
        self,
        session: Session,
        include_files_manifest: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Build messages array from session history and context.
        
        Args:
            session: Current chat session
            include_files_manifest: Whether to append files manifest
            
        Returns:
            List of messages ready for LLM call
        """
        # Get conversation history from session
        messages = session.history.get_messages_for_llm()
        
        # Optionally add files manifest
        if include_files_manifest:
            session_context = build_session_context(session)
            files_manifest = file_utils.build_files_manifest(session_context)
            if files_manifest:
                messages.append(files_manifest)
        
        return messages
