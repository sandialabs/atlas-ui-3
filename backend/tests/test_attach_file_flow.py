import base64
import uuid
import pytest

from application.chat.service import ChatService
from modules.file_storage.manager import FileManager
from modules.file_storage.mock_s3_client import MockS3StorageClient


class FakeLLM:
    async def call_plain(self, model_name, messages, temperature=0.7):
        return "ok"

    async def call_with_tools(self, model_name, messages, tools_schema, tool_choice="auto", temperature=0.7):
        from interfaces.llm import LLMResponse
        return LLMResponse(content="ok", tool_calls=None, model_used=model_name)

    async def call_with_rag(self, model_name, messages, data_sources, user_email, temperature=0.7):
        return "ok"

    async def call_with_rag_and_tools(self, model_name, messages, data_sources, tools_schema, user_email, tool_choice="auto", temperature=0.7):
        from interfaces.llm import LLMResponse
        return LLMResponse(content="ok", tool_calls=None, model_used=model_name)


@pytest.fixture
def file_manager():
    # Use in-process mock S3 for deterministic tests
    return FileManager(s3_client=MockS3StorageClient())


@pytest.fixture
def chat_service(file_manager):
    # Minimal ChatService wiring for file/session operations
    return ChatService(llm=FakeLLM(), file_manager=file_manager)


@pytest.mark.asyncio
async def test_handle_attach_file_success_creates_session_and_emits_update(chat_service, file_manager):
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    # Seed a file into the mock storage for this user
    filename = "report.txt"
    content_b64 = base64.b64encode(b"hello world").decode()
    upload_meta = await file_manager.s3_client.upload_file(
        user_email=user_email,
        filename=filename,
        content_base64=content_b64,
        content_type="text/plain",
        tags={"source": "user"},
        source_type="user",
    )
    s3_key = upload_meta["key"]

    updates = []

    async def capture_update(msg):
        updates.append(msg)

    # Act: attach the file to a brand new session (auto-creates session)
    resp = await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=s3_key,
        user_email=user_email,
        update_callback=capture_update,
    )

    # Assert: success response and files_update emitted
    assert resp.get("type") == "file_attach"
    assert resp.get("success") is True
    assert resp.get("filename") == filename

    assert any(
        u.get("type") == "intermediate_update" and u.get("update_type") == "files_update"
        for u in updates
    ), "Expected a files_update intermediate update to be emitted"

    # Session context should include the file by filename
    session = chat_service.sessions.get(session_id)
    assert session is not None
    assert filename in session.context.get("files", {})
    assert session.context["files"][filename]["key"] == s3_key


@pytest.mark.asyncio
async def test_handle_attach_file_not_found_returns_error(chat_service):
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    # Non-existent S3 key for the same user
    bad_key = f"users/{user_email}/uploads/does_not_exist_12345.txt"
    resp = await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=bad_key,
        user_email=user_email,
        update_callback=None,
    )

    assert resp.get("type") == "file_attach"
    assert resp.get("success") is False
    assert "File not found" in resp.get("error", "")


@pytest.mark.asyncio
async def test_handle_attach_file_unauthorized_other_user_key(chat_service, file_manager):
    # Upload under user1
    owner_email = "owner@example.com"
    other_email = "other@example.com"
    session_id = uuid.uuid4()

    filename = "secret.pdf"
    content_b64 = base64.b64encode(b"top-secret").decode()
    upload_meta = await file_manager.s3_client.upload_file(
        user_email=owner_email,
        filename=filename,
        content_base64=content_b64,
        content_type="application/pdf",
        tags={"source": "user"},
        source_type="user",
    )
    s3_key = upload_meta["key"]

    # Attempt to attach with a different user should fail
    resp = await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=s3_key,
        user_email=other_email,
        update_callback=None,
    )

    assert resp.get("type") == "file_attach"
    assert resp.get("success") is False
    assert "Access denied" in resp.get("error", "")


@pytest.mark.asyncio
async def test_handle_reset_session_reinitializes(chat_service):
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    # Create a session first
    await chat_service.create_session(session_id, user_email)
    assert chat_service.sessions.get(session_id) is not None

    # Reset the session
    resp = await chat_service.handle_reset_session(session_id=session_id, user_email=user_email)

    assert resp.get("type") == "session_reset"
    # After reset, a fresh active session should exist for the same id
    new_session = chat_service.sessions.get(session_id)
    assert new_session is not None
    assert new_session.active is True


@pytest.mark.asyncio
async def test_handle_download_file_success_after_attach(chat_service, file_manager):
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    # Upload and then attach to session
    filename = "notes.md"
    content_bytes = b"### Title\nSome content."
    content_b64 = base64.b64encode(content_bytes).decode()
    upload_meta = await file_manager.s3_client.upload_file(
        user_email=user_email,
        filename=filename,
        content_base64=content_b64,
        content_type="text/markdown",
        tags={"source": "user"},
        source_type="user",
    )
    s3_key = upload_meta["key"]

    await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=s3_key,
        user_email=user_email,
        update_callback=None,
    )

    # Act: download by filename (from session context)
    resp = await chat_service.handle_download_file(
        session_id=session_id,
        filename=filename,
        user_email=user_email,
    )

    assert resp.get("type") is not None
    # content_base64 should match uploaded content
    returned_b64 = resp.get("content_base64")
    assert isinstance(returned_b64, str) and len(returned_b64) > 0
    assert base64.b64decode(returned_b64) == content_bytes


@pytest.mark.asyncio
async def test_handle_download_file_not_in_session_returns_error(chat_service):
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    filename = "missing.txt"

    # No attach performed; should error that file isn't in session
    resp = await chat_service.handle_download_file(
        session_id=session_id,
        filename=filename,
        user_email=user_email,
    )

    assert resp.get("error") == "Session or file manager not available" or resp.get("error") == "File not found in session"


@pytest.mark.asyncio
async def test_upload_file_with_spaces_in_filename(file_manager):
    """Files with spaces in their names should upload successfully after sanitization."""
    user_email = "user1@example.com"
    filename_with_spaces = "my report file.txt"
    content_b64 = base64.b64encode(b"some content").decode()

    result = await file_manager.upload_file(
        user_email=user_email,
        filename=filename_with_spaces,
        content_base64=content_b64,
        source_type="user",
    )

    # Filename should be sanitized (spaces replaced with underscores)
    assert result["filename"] == "my_report_file.txt"
    assert "my_report_file.txt" in result["key"]
    assert " " not in result["key"]


@pytest.mark.asyncio
async def test_upload_multiple_files_with_spaces(file_manager):
    """upload_multiple_files should sanitize filenames containing spaces."""
    user_email = "user1@example.com"
    files = {
        "my document.pdf": base64.b64encode(b"pdf bytes").decode(),
        "another file.txt": base64.b64encode(b"text bytes").decode(),
    }

    uploaded = await file_manager.upload_multiple_files(
        user_email=user_email,
        files=files,
        source_type="user",
    )

    assert "my_document.pdf" in uploaded
    assert "another_file.txt" in uploaded
    for key in uploaded.values():
        assert " " not in key


@pytest.mark.asyncio
async def test_attach_file_with_spaces_end_to_end(chat_service, file_manager):
    """Full flow: upload a file with spaces, attach it, verify sanitized name in session."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    filename_with_spaces = "test report.txt"
    content_b64 = base64.b64encode(b"hello spaces").decode()

    upload_meta = await file_manager.upload_file(
        user_email=user_email,
        filename=filename_with_spaces,
        content_base64=content_b64,
        source_type="user",
    )
    s3_key = upload_meta["key"]

    resp = await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=s3_key,
        user_email=user_email,
        update_callback=None,
    )

    assert resp.get("success") is True
    assert " " not in s3_key

    # Verify the session stores the sanitized filename (no spaces)
    session = chat_service.sessions.get(session_id)
    assert session is not None
    session_files = session.context.get("files", {})
    # The filename key in the session should have underscores, not spaces
    stored_names = list(session_files.keys())
    for name in stored_names:
        assert " " not in name, f"Session stored filename with spaces: {name}"
