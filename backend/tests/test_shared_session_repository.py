"""
Test that sessions are shared across ChatService instances.

This test verifies the fix for the file upload registration issue where
files attached in one WebSocket connection were not visible in chat messages
because each ChatService instance had its own session repository.
"""
import pytest
import uuid
from infrastructure.app_factory import app_factory
from domain.messages.models import Message, MessageRole


@pytest.mark.asyncio
async def test_sessions_shared_across_chat_service_instances():
    """
    Test that sessions are shared across different ChatService instances.
    
    This simulates the scenario where:
    1. A file is attached via one WebSocket connection (ChatService instance 1)
    2. A chat message is sent via the same or different connection (ChatService instance 2)
    3. The file should be visible in the session retrieved by instance 2
    """
    # Create two ChatService instances (simulating two WebSocket connections)
    chat_service_1 = app_factory.create_chat_service(connection=None)
    chat_service_2 = app_factory.create_chat_service(connection=None)
    
    # Verify they share the same session repository
    assert chat_service_1.session_repository is chat_service_2.session_repository, \
        "ChatService instances should share the same session repository"
    
    user_email = "test@example.com"
    session_id = uuid.uuid4()
    
    # Step 1: Create a session and attach a file using ChatService 1
    session = await chat_service_1.create_session(session_id, user_email)
    filename = "test-document.pdf"
    session.context["files"] = {
        filename: {
            "key": "s3://bucket/test-document.pdf",
            "content_type": "application/pdf",
            "size": 1024,
            "source": "user",
        }
    }
    
    # Add a message to the session history
    session.history.add_message(Message(
        role=MessageRole.USER,
        content="what files can you see?"
    ))
    
    # Step 2: Retrieve the session using ChatService 2 (simulating a different connection)
    session_from_cs2 = await chat_service_2.session_repository.get(session_id)
    
    # Verify the session exists and contains the file
    assert session_from_cs2 is not None, "Session should be accessible from ChatService 2"
    assert session_from_cs2 is session, "Should be the same session object"
    assert filename in session_from_cs2.context.get("files", {}), \
        "File attached in ChatService 1 should be visible in ChatService 2"
    
    # Verify the session history is also shared
    messages = session_from_cs2.history.get_messages_for_llm()
    assert len(messages) == 1, "Session history should be shared"
    assert messages[0]["content"] == "what files can you see?"


@pytest.mark.asyncio
async def test_session_repository_shared_across_app_factory_calls():
    """
    Test that the session repository is truly shared at the AppFactory level.
    
    This verifies that multiple calls to create_chat_service return
    ChatService instances that all share the same underlying session storage.
    """
    # Create three ChatService instances
    cs1 = app_factory.create_chat_service()
    cs2 = app_factory.create_chat_service()
    cs3 = app_factory.create_chat_service()
    
    # All should share the same session repository instance
    assert cs1.session_repository is cs2.session_repository
    assert cs2.session_repository is cs3.session_repository
    assert cs1.session_repository is app_factory.session_repository
    
    # Create a session via cs1
    session_id = uuid.uuid4()
    await cs1.create_session(session_id, "user@example.com")
    
    # Verify it's accessible from all instances
    assert await cs1.session_repository.get(session_id) is not None
    assert await cs2.session_repository.get(session_id) is not None
    assert await cs3.session_repository.get(session_id) is not None
    
    # Verify they all return the same session object
    s1 = await cs1.session_repository.get(session_id)
    s2 = await cs2.session_repository.get(session_id)
    s3 = await cs3.session_repository.get(session_id)
    assert s1 is s2 is s3
