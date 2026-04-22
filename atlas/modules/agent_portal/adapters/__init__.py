"""Runtime adapters for the Agent Portal.

v0 ships `local_process` only. `ssh_tmux`, `kubernetes`, and `slurm`
adapters will be added in follow-up PRs, each implementing the same
`RuntimeAdapter` protocol.
"""

from atlas.modules.agent_portal.adapters.local_process import LocalProcessAdapter

__all__ = ["LocalProcessAdapter"]
