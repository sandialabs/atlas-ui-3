"""Run admission errors (issues #884, #1128).

Raised by the process-local registry and the cluster-wide coordinator alike,
so the transports map one set of exceptions however a run was refused. Kept
apart from ``registry`` so the coordinator modules need not import it.
"""


class RunRegistryError(Exception):
    """Base class for run admission failures."""


class ConversationBusyError(RunRegistryError):
    """A conversation already has a non-terminal run.

    Raised only as a backstop: the normal path for a second message to a busy
    conversation is steering (issue #824), decided before ``start`` is called.
    """


class ConcurrencyLimitError(RunRegistryError):
    """The user already has the maximum number of non-terminal runs."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f"You already have {limit} conversation runs in progress. "
            "Stop or wait for one to finish before starting another. "
            "Runs paused waiting for tool approval count toward this limit."
        )
