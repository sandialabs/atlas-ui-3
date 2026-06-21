"""Factory for creating the agent loop instance.

ATLAS uses a single native agent loop (``AgenticLoop``): the model receives
the real user tools with ``tool_choice="auto"`` and decides for itself when to
call tools and when to answer with text (text-only response = done). The older
``react``/``think-act``/``act`` strategies -- which relied on scaffolding
"control tools" and forced ``tool_choice="required"`` -- have been removed
because the forced tool choice was unsupported by several providers and the
control-tool parsing was fragile. See AGENTS.md ("Agent Loop Is Not the
Focus") for the product direction.

The factory is retained as a thin shim so that any persisted config or older
client that still sends an ``agent_loop_strategy`` value continues to work: all
values resolve to the agentic loop.
"""

import logging
from typing import Optional

from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.interfaces.transport import ChatConnectionProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from .agentic_loop import AgenticLoop
from .protocols import AgentLoopProtocol

logger = logging.getLogger(__name__)

# The single supported strategy. Any requested strategy resolves to this.
DEFAULT_STRATEGY = "agentic"


class AgentLoopFactory:
    """Creates the agent loop instance.

    Only the native agentic loop is supported. The ``strategy`` argument is
    accepted for backward compatibility but is always resolved to ``agentic``.
    """

    def __init__(
        self,
        llm: LLMProtocol,
        tool_manager: Optional[ToolManagerProtocol] = None,
        prompt_provider: Optional[PromptProvider] = None,
        connection: Optional[ChatConnectionProtocol] = None,
        config_manager=None,
    ):
        """
        Initialize factory with shared dependencies.

        Args:
            llm: LLM protocol implementation
            tool_manager: Optional tool manager
            prompt_provider: Optional prompt provider
            connection: Optional connection for sending updates
            config_manager: Optional config manager for approval settings
        """
        self.llm = llm
        self.tool_manager = tool_manager
        self.prompt_provider = prompt_provider
        self.connection = connection
        self.config_manager = config_manager
        self.skip_approval = False

        # Cached loop instance (the loop is stateless across requests).
        self._loop: Optional[AgentLoopProtocol] = None

    def create(self, strategy: str = DEFAULT_STRATEGY) -> AgentLoopProtocol:
        """
        Create the agent loop instance.

        Args:
            strategy: Accepted for backward compatibility. Any value resolves
                to the native agentic loop.

        Returns:
            AgentLoopProtocol instance
        """
        requested = (strategy or DEFAULT_STRATEGY).lower().strip()
        if requested != DEFAULT_STRATEGY:
            logger.info(
                "Agent loop strategy '%s' is deprecated; using '%s'",
                strategy,
                DEFAULT_STRATEGY,
            )

        if self._loop is None:
            self._loop = AgenticLoop(
                llm=self.llm,
                tool_manager=self.tool_manager,
                prompt_provider=self.prompt_provider,
                connection=self.connection,
                config_manager=self.config_manager,
            )
            logger.info("Created agent loop strategy: %s", DEFAULT_STRATEGY)

        self._loop.skip_approval = self.skip_approval
        return self._loop

    def get_available_strategies(self) -> list[str]:
        """Return the list of available strategy names (just the agentic loop)."""
        return [DEFAULT_STRATEGY]
