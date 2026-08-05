"""Northstar -- deterministic invariant enforcement for coding agents.

Coding agents remember your prompt. Northstar verifies that their actions still
obey it.
"""

from .checks import Finding
from .contract import Contract, ContractError
from .freeze import Oracle
from .policy import Decision, Verdict, evaluate, gate

__version__ = "0.2.0"

__all__ = [
    "Contract",
    "ContractError",
    "Decision",
    "Finding",
    "Oracle",
    "Verdict",
    "evaluate",
    "gate",
    "__version__",
]
