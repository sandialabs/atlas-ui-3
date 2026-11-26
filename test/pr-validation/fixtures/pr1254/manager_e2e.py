"""Drive the real MCPToolManager against the bundled demo servers on FastMCP 4.

Run from the atlas/ directory with PYTHONPATH set to the project root. Covers
what unit tests with stand-in objects cannot: discovery and execution over a
real MCP 2026-07-28 connection, multi round-trip elicitation, background
tasks, image content and prompts. Prints one PASS/FAIL line per check and
exits non-zero if any check failed.
"""

import asyncio
import sys
from typing import Any, Dict, List

from atlas.application.chat.elicitation_manager import get_elicitation_manager
from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager

USER = "test@test.com"
FAILURES: List[str] = []

ANSWERS: Dict[str, Any] = {
    "What's your name?": "Ada",
    "What's your age?": 36,
    "What's your favorite food?": "sushi",
    "How satisfied are you with this survey? (1-10)": 9,
    "Please provide task details": {
        "title": "Write report",
        "description": "Quarterly numbers",
        "priority": "high",
        "due_date": "2027-01-15",
    },
    "What size pizza?": "l",
    "Pick any toppings you like:": ["mushrooms", "olives"],
    "Are you sure you want to delete this item? This action cannot be undone.": True,
}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {name} ... {'PASS' if ok else 'FAIL'}{(' - ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILURES.append(name)


class Recorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def __call__(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        if event.get("type") == "elicitation_request":
            get_elicitation_manager().handle_elicitation_response(
                elicitation_id=event["elicitation_id"],
                action="accept",
                data=ANSWERS[event["message"]],
                user_email=USER,
            )

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e.get("type") == kind]


async def run_tool(manager: MCPToolManager, name: str, arguments: Dict[str, Any], call_id: str):
    recorder = Recorder()
    result = await manager.execute_tool(
        ToolCall(id=call_id, name=name, arguments=arguments),
        context={"update_callback": recorder, "user_email": USER, "conversation_id": "conv-e2e"},
    )
    return result, recorder


async def main(config_path: str) -> int:
    manager = MCPToolManager(config_path=config_path)
    manager._task_timeout = 0.2
    try:
        await manager.initialize_clients()
        await manager.discover_tools()
        await manager.discover_prompts()

        servers = sorted(manager.clients)
        check("all six demo servers connected", len(servers) == 6, str(servers))
        tool_names = {
            f"{server}_{tool.name}"
            for server, data in manager.available_tools.items()
            for tool in data.get("tools", [])
        }
        check("tools discovered over 2026-07-28", "calculator_evaluate" in tool_names, str(sorted(tool_names))[:200])
        schema = manager.get_tools_schema(["calculator_evaluate"])
        params = schema[0]["function"]["parameters"] if schema else {}
        check("tool input schema carried into the LLM schema", "expression" in params.get("properties", {}), str(params)[:200])
        check("prompts discovered", any(data.get("prompts") for data in manager.available_prompts.values()))

        result, _ = await run_tool(manager, "calculator_evaluate", {"expression": "6*7"}, "c1")
        check("plain tool call", result.success and "42" in result.content, result.content[:200])

        result, rec = await run_tool(manager, "elicitation_demo_get_user_name", {}, "c2")
        check("single elicitation", result.success and "Hello, Ada!" in result.content, result.content[:200])
        check("elicitation routed to its tool call", [e["tool_call_id"] for e in rec.of("elicitation_request")] == ["c2"])

        result, rec = await run_tool(manager, "elicitation_demo_multi_turn_survey", {}, "c3")
        check("multi-turn elicitation (4 round trips)", result.success and "Survey Complete" in result.content and len(rec.of("elicitation_request")) == 4, result.content[:200])

        result, rec = await run_tool(manager, "elicitation_demo_create_task", {}, "c4")
        check("structured form elicitation", result.success and "Title: Write report" in result.content, result.content[:200])

        result, rec = await run_tool(manager, "elicitation_demo_choose_toppings", {}, "c5")
        check("two questions in one round trip", result.success and "size l pizza with mushrooms, olives" in result.content and len(rec.of("elicitation_request")) == 2, result.content[:200])

        result, rec = await run_tool(manager, "elicitation_demo_approve_deletion", {}, "c6")
        check("boolean approval elicitation", result.success and "deleted successfully" in result.content, result.content[:200])

        results = await asyncio.gather(
            run_tool(manager, "elicitation_demo_get_user_name", {}, "p1"),
            run_tool(manager, "elicitation_demo_get_user_name", {}, "p2"),
        )
        routed = sorted(e["tool_call_id"] for _, rec in results for e in rec.of("elicitation_request"))
        own = all([e["tool_call_id"] for e in rec.of("elicitation_request")] == [cid] for (_, rec), cid in zip(results, ("p1", "p2")))
        check("parallel calls each get their own prompt", routed == ["p1", "p2"] and own, str(routed))

        result, rec = await run_tool(manager, "task_demo_data_pipeline", {"records": 50, "batch_size": 25}, "c7")
        check("background task result", result.success and "50" in result.content, result.content[:200])
        check("slow background task announced to the UI", [e["type"] for e in rec.events if e["type"].startswith("tool_task")] == ["tool_task_started", "tool_task_completed"], str([e["type"] for e in rec.events]))

        result, rec = await run_tool(manager, "image_demo_generate_test_image", {}, "c8")
        artifacts = getattr(result, "artifacts", None) or []
        check("image content becomes an artifact", result.success and len(artifacts) >= 1, f"artifacts={len(artifacts)} content={result.content[:120]}")

        result, rec = await run_tool(manager, "progress_demo_long_task", {"task": "demo", "duration_seconds": 2, "interval_seconds": 1}, "c9")
        check("progress notifications forwarded", result.success and len(rec.of("tool_progress")) >= 1, str([e["type"] for e in rec.events])[:200])
    finally:
        await manager.cleanup()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
