"""
Prefect flow that executes an ATLAS agent inside a K8s Job container.

The agent runs a configurable loop strategy (think-act, react, agentic, act)
with access to specified MCP tools. Parameters are injected by the
PrefectAgentExecutor when creating the flow run.
"""

import asyncio
import json
import logging
import os
import sys

from prefect import flow, get_run_logger

# ---------------------------------------------------------------------------
# Environment -- injected by K8s Job env from PrefectAgentExecutor
# ---------------------------------------------------------------------------
PREFECT_API_URL = os.getenv("PREFECT_API_URL", "http://prefect-server:4200/api")
AGENT_ID = os.getenv("AGENT_ID", "unknown")
AGENT_OWNER = os.getenv("AGENT_OWNER", "unknown")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")  # Scoped Keycloak token


@flow(name="atlas-agent-run", log_prints=True)
def run_agent(
    agent_id: str = "",
    owner: str = "",
    name: str = "Agent",
    template_id: str = "custom",
    max_steps: int = 10,
    loop_strategy: str = "think-act",
    mcp_servers: list | None = None,
    sandbox_policy: str = "restrictive",
    environment: dict | None = None,
) -> dict:
    """Execute an ATLAS agent loop as a Prefect flow.

    This flow is the entrypoint for agent K8s Jobs created by the Prefect
    Kubernetes worker.  It initialises the ATLAS agent machinery, connects
    to the requested MCP servers, and runs the agent loop for up to
    ``max_steps`` iterations.

    Returns:
        Dict with agent_id, steps completed, final answer (if any),
        and success status.
    """
    logger = get_run_logger()
    agent_id = agent_id or AGENT_ID
    owner = owner or AGENT_OWNER
    mcp_servers = mcp_servers or []
    environment = environment or {}

    logger.info(
        "Starting agent %s (template=%s, strategy=%s, max_steps=%d, mcp=%s)",
        agent_id,
        template_id,
        loop_strategy,
        max_steps,
        mcp_servers,
    )

    # Run the async agent loop
    result = asyncio.run(
        _run_agent_async(
            agent_id=agent_id,
            owner=owner,
            name=name,
            template_id=template_id,
            max_steps=max_steps,
            loop_strategy=loop_strategy,
            mcp_servers=mcp_servers,
            sandbox_policy=sandbox_policy,
            environment=environment,
            logger=logger,
        )
    )

    if result["success"]:
        logger.info("Agent %s completed: %d steps", agent_id, result["steps"])
    else:
        logger.warning("Agent %s failed: %s", agent_id, result.get("error"))

    return result


async def _run_agent_async(
    *,
    agent_id: str,
    owner: str,
    name: str,
    template_id: str,
    max_steps: int,
    loop_strategy: str,
    mcp_servers: list,
    sandbox_policy: str,
    environment: dict,
    logger: logging.Logger,
) -> dict:
    """Async inner loop that drives the ATLAS agent machinery."""

    result = {
        "agent_id": agent_id,
        "owner": owner,
        "template_id": template_id,
        "strategy": loop_strategy,
        "steps": 0,
        "final_answer": None,
        "success": False,
        "error": None,
    }

    try:
        # Late imports so the flow file can be parsed even if atlas
        # is not installed (e.g. during image build / testing).
        from atlas.application.chat.agent import AgentLoopFactory
        from atlas.application.chat.agent.protocols import AgentContext
        from atlas.infrastructure.app_factory import app_factory
        from atlas.modules.mcp_tools import MCPToolManager

        # Bootstrap minimal ATLAS config
        config_manager = app_factory.get_config_manager()

        # Build MCP tool manager scoped to the allowed servers
        tool_manager = MCPToolManager()

        # Resolve which tools this agent is allowed to use
        selected_tools = []
        if mcp_servers:
            try:
                all_tools = await tool_manager.get_tools_schema()
                for tool in all_tools:
                    tool_name = tool.get("function", {}).get("name", "")
                    server = tool_name.split("_", 1)[0] if "_" in tool_name else ""
                    if server in mcp_servers or tool_name in mcp_servers:
                        selected_tools.append(tool_name)
                logger.info("Agent tools resolved: %s", selected_tools)
            except Exception as exc:
                logger.warning("Could not resolve MCP tools: %s", exc)

        # Get an LLM instance from the config
        from atlas.infrastructure.llm.litellm_provider import LiteLLMProvider

        llm = LiteLLMProvider()

        # Create the agent loop
        factory = AgentLoopFactory(
            llm=llm,
            tool_manager=tool_manager,
            config_manager=config_manager,
        )
        agent_loop = factory.create(loop_strategy)

        # Build context
        import uuid

        context = AgentContext(
            session_id=uuid.UUID(agent_id.ljust(32, "0")[:32]) if len(agent_id) < 32 else uuid.uuid4(),
            user_email=owner,
            files={},
            history=[],
        )

        # Build the initial message from environment or template defaults
        system_prompt = environment.get(
            "system_prompt",
            f"You are an ATLAS agent ({name}). Complete the assigned task using available tools.",
        )
        user_prompt = environment.get("task", "Await instructions.")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Collect events for logging
        events = []

        async def event_handler(event):
            events.append({"type": event.type, "step": event.payload.get("step")})
            logger.info("Agent event: %s (step=%s)", event.type, event.payload.get("step"))

        # Determine model from environment or config
        model = environment.get("model", config_manager.app_settings.default_model if hasattr(config_manager.app_settings, "default_model") else "claude-sonnet-4-20250514")

        # Run the agent loop
        agent_result = await agent_loop.run(
            model=model,
            messages=messages,
            context=context,
            selected_tools=selected_tools,
            max_steps=max_steps,
            temperature=0.7,
            event_handler=event_handler,
        )

        result["steps"] = agent_result.steps
        result["final_answer"] = agent_result.final_answer
        result["success"] = True
        result["events_count"] = len(events)

    except ImportError as exc:
        result["error"] = f"Missing dependency: {exc}"
        logger.error("Agent %s import error: %s", agent_id, exc)
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Agent %s error: %s", agent_id, exc, exc_info=True)

    return result


if __name__ == "__main__":
    # Local testing
    output = run_agent(
        agent_id="test-001",
        owner="test@example.com",
        name="Test Agent",
        template_id="custom",
        max_steps=3,
        loop_strategy="think-act",
        mcp_servers=[],
        environment={"task": "Say hello and describe your capabilities."},
    )
    print(json.dumps(output, indent=2))
