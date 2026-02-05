"""Tests for AtlasClient and CLI arg parsing."""

import json
from uuid import uuid4

import pytest

from atlas.infrastructure.events.cli_event_publisher import CLIEventPublisher


# ---------------------------------------------------------------------------
# CLIEventPublisher unit tests
# ---------------------------------------------------------------------------

class TestCLIEventPublisher:
    """Tests for CLIEventPublisher in collecting mode."""

    @pytest.fixture
    def publisher(self):
        return CLIEventPublisher(streaming=False)

    @pytest.mark.asyncio
    async def test_publish_chat_response_accumulates(self, publisher):
        await publisher.publish_chat_response("Hello ")
        await publisher.publish_chat_response("world")
        result = publisher.get_result()
        assert result.message == "Hello world"

    @pytest.mark.asyncio
    async def test_publish_tool_start_and_complete(self, publisher):
        await publisher.publish_tool_start("my_tool")
        assert len(publisher.get_result().tool_calls) == 1
        assert publisher.get_result().tool_calls[0]["status"] == "started"

        await publisher.publish_tool_complete("my_tool", result="ok")
        assert publisher.get_result().tool_calls[0]["status"] == "complete"
        assert publisher.get_result().tool_calls[0]["result"] == "ok"

    @pytest.mark.asyncio
    async def test_publish_files_update(self, publisher):
        await publisher.publish_files_update({"report.pdf": {"key": "s3/report.pdf"}})
        assert "report.pdf" in publisher.get_result().files

    @pytest.mark.asyncio
    async def test_publish_canvas_content(self, publisher):
        await publisher.publish_canvas_content("<h1>Hi</h1>")
        assert publisher.get_result().canvas_content == "<h1>Hi</h1>"

    @pytest.mark.asyncio
    async def test_send_json_records_event(self, publisher):
        await publisher.send_json({"type": "custom", "data": 1})
        assert len(publisher.get_result().raw_events) == 1

    @pytest.mark.asyncio
    async def test_streaming_writes_to_stdout(self, capsys):
        pub = CLIEventPublisher(streaming=True)
        await pub.publish_chat_response("token1")
        captured = capsys.readouterr()
        assert "token1" in captured.out

    @pytest.mark.asyncio
    async def test_quiet_suppresses_status(self, capsys):
        pub = CLIEventPublisher(streaming=True, quiet=True)
        await pub.publish_tool_start("some_tool")
        captured = capsys.readouterr()
        # quiet mode: no stderr output for tool status
        assert "some_tool" not in captured.err


# ---------------------------------------------------------------------------
# CLI arg parsing tests
# ---------------------------------------------------------------------------

class TestCLIArgParsing:
    """Tests for atlas_chat_cli argument parsing."""

    def test_basic_prompt(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["Hello world"])
        assert args.prompt == "Hello world"
        assert args.json_output is False

    def test_tools_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "Do stuff", "--tools", "toolA,toolB"
        ])
        assert args.tools == "toolA,toolB"

    def test_json_output_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "--json"])
        assert args.json_output is True

    def test_output_file_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "-o", "/tmp/out.txt"])
        assert args.output == "/tmp/out.txt"

    def test_list_tools_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--list-tools"])
        assert args.list_tools is True

    def test_env_file_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "--env-file", "/path/to/custom.env"])
        assert args.env_file == "/path/to/custom.env"

    def test_env_file_flag_equals_syntax(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "--env-file=/other/path.env"])
        assert args.env_file == "/other/path.env"

    def test_data_sources_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "--data-sources", "source1,source2"])
        assert args.data_sources == "source1,source2"

    def test_only_rag_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["prompt", "--only-rag"])
        assert args.only_rag is True

    def test_list_data_sources_flag(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--list-data-sources"])
        assert args.list_data_sources is True

    def test_combined_tools_and_data_sources(self):
        from atlas_chat_cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "prompt",
            "--tools", "calculator_evaluate",
            "--data-sources", "atlas_rag",
        ])
        assert args.tools == "calculator_evaluate"
        assert args.data_sources == "atlas_rag"
        assert args.only_rag is False


# ---------------------------------------------------------------------------
# ChatResult serialization tests
# ---------------------------------------------------------------------------

class TestChatResult:
    def test_to_dict(self):
        from atlas_client import ChatResult

        sid = uuid4()
        result = ChatResult(
            message="Hi",
            tool_calls=[{"tool": "x", "status": "complete"}],
            files={"a.txt": {}},
            canvas_content="<p>c</p>",
            session_id=sid,
        )
        d = result.to_dict()
        assert d["message"] == "Hi"
        assert d["session_id"] == str(sid)
        assert len(d["tool_calls"]) == 1

    def test_to_dict_json_serializable(self):
        from atlas_client import ChatResult

        result = ChatResult(message="ok")
        json.dumps(result.to_dict())  # should not raise
