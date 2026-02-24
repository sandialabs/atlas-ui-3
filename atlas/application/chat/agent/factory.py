"""Factory for creating agent loop instances based on strategy."""

import logging
from typing import Optional

from atlas.interfaces.llm import LLMProtocol
from atlas.interfaces.tools import ToolManagerProtocol
from atlas.interfaces.transport import ChatConnectionProtocol
from atlas.modules.prompts.prompt_provider import PromptProvider

from .act_loop import ActAgentLoop
from .agentic_loop import AgenticLoop
from .protocols import AgentLoopProtocol
from .react_loop import ReActAgentLoop
from .think_act_loop import ThinkActAgentLoop

logger = logging.getLogger(__name__)


class AgentLoopFactory:
    """
    Factory for creating agent loop instances.

    This factory pattern allows for easy addition of new agent loop strategies
    without modifying existing code. Simply add a new strategy to the registry.
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

        # Registry of available strategies
        self._strategy_registry = {
            "react": ReActAgentLoop,
            "think-act": ThinkActAgentLoop,
            "think_act": ThinkActAgentLoop,
            "thinkact": ThinkActAgentLoop,
            "act": ActAgentLoop,
            "agentic": AgenticLoop,
        }

        # Cache of instantiated loops for performance
        self._loop_cache: dict[str, AgentLoopProtocol] = {}

    def create(self, strategy: str = "think-act") -> AgentLoopProtocol:
        """
        Create an agent loop instance for the given strategy.

        Args:
            strategy: Strategy name (react, think-act, act, etc.)

        Returns:
            AgentLoopProtocol instance

        Note:
            If the strategy is not recognized, falls back to 'react' with a warning.
        """
        strategy_normalized = strategy.lower().strip()

        # Check cache first
        if strategy_normalized in self._loop_cache:
            logger.info(f"Using agent loop strategy: {strategy_normalized}")
            return self._loop_cache[strategy_normalized]

        # Look up strategy in registry
        loop_class = self._strategy_registry.get(strategy_normalized)

        if loop_class is None:
            logger.warning(
                f"Unknown agent loop strategy '{strategy}', falling back to 'react'"
            )
            loop_class = self._strategy_registry["react"]
            strategy_normalized = "react"

        # Instantiate the loop
        loop_instance = loop_class(
            llm=self.llm,
            tool_manager=self.tool_manager,
            prompt_provider=self.prompt_provider,
            connection=self.connection,
            config_manager=self.config_manager,
        )

        loop_instance.skip_approval = self.skip_approval

        # Cache for future use
        self._loop_cache[strategy_normalized] = loop_instance

        logger.info(f"Created and using agent loop strategy: {strategy_normalized}")
        return loop_instance

    def get_available_strategies(self) -> list[str]:
        """
        Get list of available strategy names.

        Returns:
            List of strategy identifiers
        """
        # Return unique strategy names (deduplicated)
        unique_strategies = set()
        for strategy in self._strategy_registry.keys():
            # Normalize to primary name
            if strategy in ("react",):
                unique_strategies.add("react")
            elif strategy in ("think-act", "think_act", "thinkact"):
                unique_strategies.add("think-act")
            elif strategy in ("act",):
                unique_strategies.add("act")
            elif strategy in ("agentic",):
                unique_strategies.add("agentic")
        return sorted(unique_strategies)

    def register_strategy(self, name: str, loop_class: type[AgentLoopProtocol]) -> None:
        """
        Register a new agent loop strategy.

        This allows for dynamic extension of available strategies.

        Args:
            name: Strategy identifier
            loop_class: Agent loop class to instantiate
        """
        name_normalized = name.lower().strip()
        self._strategy_registry[name_normalized] = loop_class
        logger.info(f"Registered new agent loop strategy: {name_normalized}")
