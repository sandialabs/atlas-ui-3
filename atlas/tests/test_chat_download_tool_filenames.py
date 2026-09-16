"""Chat-side download of files an MCP tool produced.

The library (File Manager) downloads by S3 key, so it always works. Chat
downloads by *filename*: the browser sends the name it was shown and the
backend looks it up in ``session.context["files"]``. Storage sanitizes the
name on the way in (spaces and punctuation become underscores), while the
name the tool advertises -- the artifact ``name``, ``meta_data.output_files``
-- is the original. Those two disagreeing is what makes a file downloadable
from the library and not from chat.
"""

import base64
import uuid

import pytest

from atlas.application.chat.service import ChatService
from atlas.application.chat.utilities import file_processor
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient


class _FakeLLM:
    async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
        return "ok"


class _ToolResult:
    """The shape ``process_tool_artifacts`` consumes."""

    def __init__(self, artifacts, display_config=None):
        self.artifacts = artifacts
        self.display_config = display_config or {}
        self.result = {}


@pytest.fixture
def file_manager():
    return FileManager(s3_client=MockS3StorageClient())


@pytest.fixture
def chat_service(file_manager):
    return ChatService(llm=_FakeLLM(), file_manager=file_manager)


ADVERTISED_NAME = "Q3 Sales Report (final).csv"


async def _run_tool_producing(chat_service, session_id, user_email, name):
    """Ingest one tool artifact named ``name`` into ``session_id``."""
    session = await chat_service.create_session(session_id, user_email)
    context = {
        "session_id": str(session_id),
        "user_email": user_email,
        "files": dict(session.context.get("files", {})),
    }
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult(
            [{"name": name, "b64": base64.b64encode(b"a,b\n1,2\n").decode(), "mime": "text/csv"}]
        ),
        file_manager=chat_service.file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})
    return session


@pytest.mark.asyncio
async def test_chat_download_uses_name_the_tool_advertised(chat_service):
    """The name the UI renders a download button for must resolve."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=ADVERTISED_NAME, user_email=user_email
    )

    assert not response.get("error"), response.get("error")
    assert base64.b64decode(response["content_base64"]) == b"a,b\n1,2\n"
    # The browser saves under the name the user was shown.
    assert response["filename"] == ADVERTISED_NAME


@pytest.mark.asyncio
async def test_chat_download_still_works_by_stored_name(chat_service):
    """The sanitized name -- what the library lists -- keeps working."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)
    stored_name = chat_service.file_manager.sanitize_filename(ADVERTISED_NAME)
    assert stored_name != ADVERTISED_NAME, "fixture no longer exercises sanitization"

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=stored_name, user_email=user_email
    )

    assert not response.get("error"), response.get("error")
    assert response["filename"] == stored_name


@pytest.mark.parametrize("order", [("a b.txt", "a_b.txt"), ("a_b.txt", "a b.txt")])
@pytest.mark.asyncio
async def test_names_that_sanitize_alike_keep_their_own_files(chat_service, order):
    """Two artifacts whose names sanitize alike must not displace each other.

    ``a b.txt`` and ``a_b.txt`` both want the stored key ``a_b.txt``. Whichever
    order they arrive in, each advertised name has to download its own bytes --
    a plain overwrite would leave the loser's download control pointing at the
    survivor.
    """
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    bodies = {"a b.txt": b"spaced", "a_b.txt": b"underscored"}

    session = await chat_service.create_session(session_id, user_email)
    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    for name in order:
        context = await file_processor.process_tool_artifacts(
            session_context=context,
            tool_result=_ToolResult(
                [{"name": name, "b64": base64.b64encode(bodies[name]).decode(),
                  "mime": "text/plain"}]
            ),
            file_manager=chat_service.file_manager,
            update_callback=None,
        )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})
    assert len(session.context["files"]) == 2, session.context["files"]

    for name, body in bodies.items():
        response = await chat_service.handle_download_file(
            session_id=session_id, filename=name, user_email=user_email
        )
        assert not response.get("error"), (name, response.get("error"))
        assert base64.b64decode(response["content_base64"]) == body, name
        assert response["filename"] == name


@pytest.mark.asyncio
async def test_re_uploading_the_same_name_refreshes_in_place(chat_service):
    """A tool rewriting its own output updates the entry, not a second one."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    session = await chat_service.create_session(session_id, user_email)
    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    for body in (b"first", b"second"):
        context = await file_processor.process_tool_artifacts(
            session_context=context,
            tool_result=_ToolResult(
                [{"name": ADVERTISED_NAME, "b64": base64.b64encode(body).decode(),
                  "mime": "text/csv"}]
            ),
            file_manager=chat_service.file_manager,
            update_callback=None,
        )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})
    assert len(session.context["files"]) == 1, session.context["files"]

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=ADVERTISED_NAME, user_email=user_email
    )
    assert base64.b64decode(response["content_base64"]) == b"second"


@pytest.mark.asyncio
async def test_legacy_entry_without_recorded_original_still_resolves(chat_service):
    """Sessions ingested before originals were recorded keep working."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)
    session = await chat_service.session_repository.get(session_id)
    for ref in session.context["files"].values():
        ref.pop("original_filename", None)

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=ADVERTISED_NAME, user_email=user_email
    )

    assert not response.get("error"), response.get("error")
    assert base64.b64decode(response["content_base64"]) == b"a,b\n1,2\n"


@pytest.mark.parametrize("bad", [None, 42, ["a.csv"], {"a": 1}, ""])
@pytest.mark.asyncio
async def test_chat_download_rejects_non_string_filename(chat_service, bad):
    """A malformed frame gets an error reply, not an exception."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=bad, user_email=user_email
    )

    assert response["type"] == "file_download"
    assert response.get("error") == "A filename is required"


@pytest.mark.asyncio
async def test_chat_download_rejects_unknown_file(chat_service):
    """Tolerant matching must not turn a miss into someone else's file."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)

    response = await chat_service.handle_download_file(
        session_id=session_id, filename="not-a-real-file.csv", user_email=user_email
    )

    assert response.get("error") == "File not found in session"


@pytest.mark.asyncio
async def test_tool_alias_does_not_shadow_a_user_attachment(chat_service, file_manager):
    """A user's own file keeps its name when a tool advertises the same one.

    ``handle_attach_file`` keys an attachment by its real filename and records
    no advertised original. A tool artifact named identically is stored under
    the sanitized key with that name as its original -- it must not answer for
    the attachment sitting at the exact key.
    """
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    name = "report final.csv"

    upload = await file_manager.s3_client.upload_file(
        user_email=user_email,
        filename=name,
        content_base64=base64.b64encode(b"mine").decode(),
        content_type="text/csv",
        tags={"source": "user"},
        source_type="user",
    )
    await chat_service.handle_attach_file(
        session_id=session_id,
        s3_key=upload["key"],
        user_email=user_email,
        update_callback=None,
    )
    session = await chat_service.session_repository.get(session_id)
    assert name in session.context["files"], session.context["files"]

    context = {
        "session_id": str(session_id),
        "user_email": user_email,
        "files": dict(session.context["files"]),
    }
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult(
            [{"name": name, "b64": base64.b64encode(b"theirs").decode(), "mime": "text/csv"}]
        ),
        file_manager=file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=name, user_email=user_email
    )

    assert base64.b64decode(response["content_base64"]) == b"mine"


@pytest.mark.asyncio
async def test_re_emitting_a_collided_name_refreshes_its_own_entry(chat_service):
    """A re-keyed artifact updates where it lives, not a third key."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    session = await chat_service.create_session(session_id, user_email)
    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    for name, body in (("a b.txt", b"spaced"), ("a_b.txt", b"first"), ("a_b.txt", b"second")):
        context = await file_processor.process_tool_artifacts(
            session_context=context,
            tool_result=_ToolResult(
                [{"name": name, "b64": base64.b64encode(body).decode(), "mime": "text/plain"}]
            ),
            file_manager=chat_service.file_manager,
            update_callback=None,
        )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})
    assert len(session.context["files"]) == 2, session.context["files"]

    for name, body in (("a b.txt", b"spaced"), ("a_b.txt", b"second")):
        response = await chat_service.handle_download_file(
            session_id=session_id, filename=name, user_email=user_email
        )
        assert not response.get("error"), (name, response.get("error"))
        assert base64.b64decode(response["content_base64"]) == body, name


@pytest.mark.asyncio
async def test_canvas_event_carries_the_re_keyed_artifact_key(chat_service):
    """The canvas must preview the artifact it names, not the key's occupant."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    updates = []

    async def capture(msg):
        updates.append(msg)

    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    keys = {}
    for name, body in (("a b.png", b"spaced"), ("a_b.png", b"underscored")):
        context = await file_processor.process_tool_artifacts(
            session_context=context,
            tool_result=_ToolResult(
                [{"name": name, "b64": base64.b64encode(body).decode(), "mime": "image/png"}],
                display_config={"open_canvas": True, "primary_file": name},
            ),
            file_manager=chat_service.file_manager,
            update_callback=capture,
        )
        stored, ref = file_processor.resolve_session_file(
            context["files"], name, chat_service.file_manager.sanitize_filename
        )
        keys[name] = ref["key"]

    assert keys["a b.png"] != keys["a_b.png"]
    canvas = [
        u for u in updates
        if u.get("update_type") == "canvas_files" and u["data"].get("files")
    ]
    assert canvas, "expected canvas_files events"
    emitted = canvas[-1]["data"]["files"][0]
    assert emitted["filename"] == "a_b.png"
    assert emitted["s3_key"] == keys["a_b.png"]


@pytest.mark.asyncio
async def test_one_result_carrying_both_colliding_names_keeps_both(chat_service):
    """Both artifacts survive even when a single tool result carries them.

    ``upload_files_from_base64`` returns a dict keyed by the sanitized name, so
    two artifacts in one result reached the session as one entry -- the earlier
    erased before the session-level collision handling ever saw it.
    """
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    bodies = {"a b.txt": b"spaced", "a_b.txt": b"underscored"}

    session = await chat_service.create_session(session_id, user_email)
    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult([
            {"name": name, "b64": base64.b64encode(body).decode(), "mime": "text/plain"}
            for name, body in bodies.items()
        ]),
        file_manager=chat_service.file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})
    assert len(session.context["files"]) == 2, session.context["files"]

    for name, body in bodies.items():
        response = await chat_service.handle_download_file(
            session_id=session_id, filename=name, user_email=user_email
        )
        assert not response.get("error"), (name, response.get("error"))
        assert base64.b64decode(response["content_base64"]) == body, name


@pytest.mark.asyncio
async def test_storage_key_decides_when_the_caller_has_one(chat_service):
    """A row that knows its key gets that file, not a name-matched one.

    In one result, `a b.txt` is stored at `a_b.txt` and `a_b.txt` at
    `a_b_1.txt`. Clicking the `a_b.txt` row sends that stored name, which by
    name alone is indistinguishable from the second artifact's advertised
    name -- the key is what settles it.
    """
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    bodies = {"a b.txt": b"spaced", "a_b.txt": b"underscored"}

    session = await chat_service.create_session(session_id, user_email)
    context = {"session_id": str(session_id), "user_email": user_email, "files": {}}
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult([
            {"name": name, "b64": base64.b64encode(body).decode(), "mime": "text/plain"}
            for name, body in bodies.items()
        ]),
        file_manager=chat_service.file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})

    for stored_name, ref in session.context["files"].items():
        response = await chat_service.handle_download_file(
            session_id=session_id,
            filename=stored_name,
            user_email=user_email,
            s3_key=ref["key"],
        )
        assert not response.get("error"), (stored_name, response.get("error"))
        expected = bodies[ref["original_filename"]]
        assert base64.b64decode(response["content_base64"]) == expected, stored_name


@pytest.mark.asyncio
async def test_storage_key_outside_the_session_is_refused(chat_service, file_manager):
    """The key disambiguates within the session; it is not a way out of it."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)

    elsewhere = await file_manager.s3_client.upload_file(
        user_email=user_email,
        filename="other.txt",
        content_base64=base64.b64encode(b"not in this session").decode(),
        content_type="text/plain",
        tags={"source": "user"},
        source_type="user",
    )

    response = await chat_service.handle_download_file(
        session_id=session_id,
        filename=ADVERTISED_NAME,
        user_email=user_email,
        s3_key=elsewhere["key"],
    )

    assert response.get("error") == "File not found in session"
    assert "content_base64" not in response


@pytest.mark.asyncio
async def test_canvas_event_describes_the_artifact_just_ingested(chat_service, file_manager):
    """A same-named user attachment must not lend its key to a tool artifact."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()
    name = "chart final.png"
    updates = []

    async def capture(msg):
        updates.append(msg)

    attached = await file_manager.s3_client.upload_file(
        user_email=user_email,
        filename=name,
        content_base64=base64.b64encode(b"mine").decode(),
        content_type="image/png",
        tags={"source": "user"},
        source_type="user",
    )
    await chat_service.handle_attach_file(
        session_id=session_id, s3_key=attached["key"],
        user_email=user_email, update_callback=None,
    )
    session = await chat_service.session_repository.get(session_id)

    context = {
        "session_id": str(session_id),
        "user_email": user_email,
        "files": dict(session.context["files"]),
    }
    await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult(
            [{"name": name, "b64": base64.b64encode(b"theirs").decode(), "mime": "image/png"}],
            display_config={"open_canvas": True, "primary_file": name},
        ),
        file_manager=file_manager,
        update_callback=capture,
    )

    canvas = [
        u for u in updates
        if u.get("update_type") == "canvas_files" and u["data"].get("files")
    ]
    assert canvas, "expected a canvas_files event"
    emitted = canvas[-1]["data"]["files"][0]
    assert emitted["s3_key"] != attached["key"], "canvas showed the attachment's key"


@pytest.mark.asyncio
async def test_legacy_matching_survives_a_new_artifact_in_the_session(chat_service):
    """One new-style artifact must not switch off compatibility for old ones."""
    user_email = "user1@example.com"
    session_id = uuid.uuid4()

    await _run_tool_producing(chat_service, session_id, user_email, ADVERTISED_NAME)
    session = await chat_service.session_repository.get(session_id)
    for ref in session.context["files"].values():
        ref.pop("original_filename", None)

    context = {
        "session_id": str(session_id),
        "user_email": user_email,
        "files": dict(session.context["files"]),
    }
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult(
            [{"name": "unrelated.txt", "b64": base64.b64encode(b"new").decode(),
              "mime": "text/plain"}]
        ),
        file_manager=chat_service.file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})

    response = await chat_service.handle_download_file(
        session_id=session_id, filename=ADVERTISED_NAME, user_email=user_email
    )

    assert not response.get("error"), response.get("error")
    assert base64.b64decode(response["content_base64"]) == b"a,b\n1,2\n"
