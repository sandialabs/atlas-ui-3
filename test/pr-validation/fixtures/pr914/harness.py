"""End-to-end harness for PR #914: tool-returned images must reach the LLM.

Runs a real FastMCP server in-process whose tool returns a real ImageContent
PNG, drives it with the real FastMCP client, and pushes the resulting
CallToolResult through the real ATLAS tool-execution path: MCPToolManager.
execute_tool (text normalization + artifact extraction), then the agentic
loop's image-injection hook. Before the fix, the model-visible tool result
was {"results": {}} and nothing else existed for the LLM; after it, the
transcript carries a synthetic user message holding the image as an
image_url data-URI block -- the shape providers accept after LiteLLM's
translation.
"""
import asyncio
import base64
import io
import json
import sys
from typing import Any, Dict, List

from fastmcp import FastMCP
from fastmcp.client import Client

from atlas.application.chat.utilities.tool_image_context import ToolImageInjector
from atlas.modules.mcp_tools.client import MCPToolManager


def _png_b64() -> str:
    """A real 8x8 PNG built with Pillow, so the payload is a genuine image."""
    from PIL import Image

    image = Image.new("RGB", (8, 8), color=(10, 120, 240))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


mcp = FastMCP("pr914-image-server")


@mcp.tool
def take_screenshot() -> Any:
    """Return an ImageContent block, the shape screenshot tools return."""
    from mcp.types import ImageContent

    return ImageContent(type="image", data=_png_b64(), mimeType="image/png")


@mcp.tool
def text_only() -> Dict[str, Any]:
    """Text-only control: must produce no image artifacts and no injection."""
    return {"results": {"status": "ok"}}


def _run_tool(raw_result: Any, tool_name: str):
    mgr = MCPToolManager(config_path="/tmp/nonexistent_pr914_mcp.json")
    normalized = mgr._normalize_mcp_tool_result(raw_result)
    content = json.dumps(normalized, ensure_ascii=False, default=str)
    artifacts, display_config, meta_data = mgr._extract_v2_components(
        raw_result, tool_name,
    )
    return content, artifacts, display_config


async def main() -> int:
    failures: List[str] = []

    async with Client(mcp) as client:
        # 1. Image tool: the bug path. The normalized text result normalizes
        #    to an empty results dict (prompt bloat policy), and the image
        #    travels only as an artifact -- exactly what the LLM never saw
        #    before this fix.
        image_call = await client.call_tool("take_screenshot", {})
        content, artifacts, display_config = _run_tool(
            image_call, "take_screenshot",
        )
        decoded = json.loads(content)
        if decoded.get("results") != {}:
            failures.append(
                f"image tool text result should normalize to {{'results': {{}}}}, "
                f"got {decoded!r}"
            )
        if not artifacts or artifacts[0].get("mime") != "image/png":
            failures.append(f"image tool should yield one png artifact, got {artifacts!r}")

        # 2. Now the fix: feed the ToolResult through the injector exactly as
        #    the agentic loop does, with vision enabled.
        from atlas.domain.messages.models import ToolResult

        tool_result = ToolResult(
            tool_call_id="call_screenshot_1",
            content=content,
            success=True,
            artifacts=artifacts,
            display_config=display_config,
        )

        messages = [
            {"role": "user", "content": "screenshot the model"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_screenshot_1"}]},
            {"role": "tool", "content": content, "tool_call_id": "call_screenshot_1"},
        ]
        injector = ToolImageInjector(enabled=True)
        injector.after_tool_results(
            messages, [tool_result],
            tool_names={"call_screenshot_1": "fusion360_fusion_mcp_read"},
        )
        injected = messages[-1]
        blocks = injected.get("content")
        if injected.get("role") != "user" or not isinstance(blocks, list):
            failures.append(f"expected synthetic user image message, got {injected!r}")
        else:
            image_blocks = [
                b for b in blocks
                if isinstance(b, dict) and b.get("type") == "image_url"
            ]
            if not image_blocks:
                failures.append("no image_url block in the injected message")
            else:
                url = image_blocks[0]["image_url"]["url"]
                if not url.startswith("data:image/png;base64,"):
                    failures.append(f"bad data URI prefix: {url[:60]!r}")
                # The b64 in the transcript must decode to the exact PNG.
                sent_b64 = url.split(";base64,", 1)[1]
                if base64.b64decode(sent_b64) != base64.b64decode(_png_b64()):
                    failures.append("transcript image bytes differ from the tool's PNG")
                if "fusion360_fusion_mcp_read" not in blocks[0].get("text", ""):
                    failures.append("intro text does not name the tool")

        # 3. Vision disabled: no image message, but the tool result gains a
        #    note so the model knows an image exists and why it cannot see it.
        messages_off = [
            {"role": "user", "content": "screenshot"},
            {"role": "tool", "content": content, "tool_call_id": "call_screenshot_1"},
        ]
        ToolImageInjector(enabled=False).after_tool_results(
            messages_off, [tool_result],
        )
        if len(messages_off) != 2:
            failures.append("vision-off path must not add messages")
        if "does not support vision" not in messages_off[-1]["content"]:
            failures.append("vision-off path must annotate the tool result")

        # 4. Text-only tool: nothing injected either way.
        text_call = await client.call_tool("text_only", {})
        t_content, t_artifacts, _ = _run_tool(text_call, "text_only")
        messages_text = [{"role": "tool", "content": t_content, "tool_call_id": "t1"}]
        ToolImageInjector(enabled=True).after_tool_results(
            messages_text,
            [ToolResult(tool_call_id="t1", content=t_content, artifacts=t_artifacts)],
        )
        if len(messages_text) != 1:
            failures.append("text-only tool must not trigger injection")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("All PR #914 harness checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
