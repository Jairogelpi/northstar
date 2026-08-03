"""From findings to verdicts, and the fast pre-action gate.

Two moments, two jobs:

* `gate` runs *before* a tool call, on the path or command alone. It is the only
  thing that must be fast, and the only thing that can stop damage rather than
  observe it.
* `evaluate` runs *after*, over the whole tree against the baseline. This is
  where trajectory-level drift surfaces.

Neither reads the agent's conversation. The judge must not share context with
the agent it judges, or it drifts alongside it -- which is why every
LLM-as-judge drift detector degrades exactly when it is needed most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from . import checks
from .contract import APPROVAL_REQUIRED, FORBIDDEN, Contract
from .freeze import Oracle
from .util import matches_any, normalize


class Decision(str, Enum):
    ALLOW = "ALLOW"
    WARN_DRIFT = "WARN_DRIFT"
    UNKNOWN = "UNKNOWN"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


#: Ascending severity. The verdict for a set of findings is the worst of them.
SEVERITY = {
    Decision.ALLOW: 0,
    Decision.WARN_DRIFT: 1,
    Decision.UNKNOWN: 2,
    Decision.REQUIRE_APPROVAL: 3,
    Decision.DENY: 4,
}

BLOCKING = (Decision.DENY, Decision.REQUIRE_APPROVAL)

_RULE_DECISION = {
    FORBIDDEN: Decision.DENY,
    APPROVAL_REQUIRED: Decision.REQUIRE_APPROVAL,
}

#: Which contract rule governs each finding kind.
_RULE_FOR_KIND: dict[str, tuple[str, str]] = {
    checks.PUBLIC_API: ("public_api", "change"),
    checks.API_ADDITION: ("public_api", "additions"),
    checks.DEPENDENCY: ("dependencies", "additions"),
    checks.MODULE_EDGE: ("module_graph", "new_edges"),
    checks.BEHAVIOR: ("behavior", "change"),
}

#: Tool names, across agents, that write to the filesystem.
WRITE_TOOLS = {"edit", "write", "notebookedit", "multiedit", "str_replace_editor", "apply_patch"}
SHELL_TOOLS = {"bash", "shell", "powershell", "run_command", "local_shell", "exec"}


@dataclass
class Judgement:
    """A finding plus the verdict it earned, and why."""

    decision: Decision
    reason: str
    finding: checks.Finding | None = None
    amendment_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"decision": self.decision.value, "reason": self.reason}
        if self.finding is not None:
            data["finding"] = self.finding.to_dict()
        if self.amendment_version is not None:
            data["signed_by_amendment"] = self.amendment_version
        return data


@dataclass
class Verdict:
    decision: Decision
    judgements: list[Judgement] = field(default_factory=list)

    @property
    def blocking(self) -> list[Judgement]:
        return [j for j in self.judgements if j.decision in BLOCKING]

    @property
    def is_blocking(self) -> bool:
        return self.decision in BLOCKING

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "judgements": [j.to_dict() for j in self.judgements],
        }

    def summary(self) -> str:
        if not self.judgements:
            return "on course: no divergence from the contract"
        return "\n".join(
            f"[{j.decision.value}] {j.finding.kind if j.finding else 'gate'}: {j.reason}"
            for j in self.judgements
        )


def _worst(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: SEVERITY[d], default=Decision.ALLOW)


def judge(contract: Contract, finding: checks.Finding) -> Judgement:
    """Verdict for one finding, honouring any signed amendment that covers it."""
    # Checked before any grant lookup: these refusals are of an action, not of a
    # state, so no signature can retroactively authorise them.
    if finding.kind in checks.NOT_AMENDABLE:
        return Judgement(Decision.DENY, finding.detail, finding)

    amendment = contract.is_granted(finding.kind, finding.identifier)
    if amendment is not None:
        return Judgement(
            Decision.ALLOW,
            f"{finding.detail} -- signed in amendment v{amendment.version}: {amendment.reason}",
            finding,
            amendment.version,
        )

    if finding.kind == checks.PROTECTED_PATH:
        return Judgement(Decision.DENY, finding.detail, finding)
    if finding.kind == checks.SCOPE:
        return Judgement(Decision.REQUIRE_APPROVAL, finding.detail, finding)
    if finding.kind == checks.UNKNOWN_KIND:
        return Judgement(Decision.UNKNOWN, finding.detail, finding)

    section, key = _RULE_FOR_KIND[finding.kind]
    rule = contract.rule(section, key)
    decision = _RULE_DECISION.get(rule, Decision.ALLOW)
    if decision is Decision.ALLOW:
        return Judgement(Decision.ALLOW, f"{finding.detail} (permitted by contract)", finding)
    return Judgement(decision, finding.detail, finding)


def evaluate(contract: Contract, oracle: Oracle, root: Path) -> Verdict:
    """Full state-versus-baseline verdict for the working tree."""
    state = checks.read_tree(root, contract.api_scope)
    found = checks.run_all(contract, oracle, state)
    judgements = [judge(contract, f) for f in found]
    return Verdict(_worst([j.decision for j in judgements]), judgements)


# ------------------------------------------------------------------ pre-action


def _paths_from(params: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("file_path", "path", "notebook_path", "filePath", "target_file"):
        value = params.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    edits = params.get("edits")
    if isinstance(edits, list):
        for item in edits:
            if isinstance(item, dict):
                out.extend(_paths_from(item))
    return out


def _relative(path: str, root: Path) -> str:
    candidate = Path(path)
    try:
        if candidate.is_absolute():
            return normalize(candidate.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return normalize(path)
    return normalize(path)


def gate(contract: Contract, tool: str, params: dict[str, Any], root: Path) -> Verdict:
    """Pre-action gate: block on path and command alone, before anything is written."""
    name = (tool or "").strip().lower()
    judgements: list[Judgement] = []

    if name in WRITE_TOOLS:
        for raw in _paths_from(params):
            relative = _relative(raw, root)
            hit = matches_any(relative, contract.protected_paths)
            if hit is not None:
                judgements.append(
                    Judgement(
                        Decision.DENY,
                        f"{relative} is protected by `{hit}`",
                        checks.Finding(checks.PROTECTED_PATH, relative, f"write blocked by `{hit}`"),
                    )
                )

    if name in SHELL_TOOLS:
        command = str(params.get("command") or params.get("cmd") or "")
        judgements.extend(_judge_command(contract, command))

    return Verdict(_worst([j.decision for j in judgements]), judgements)


def _judge_command(contract: Contract, command: str) -> list[Judgement]:
    stripped = command.strip()
    if not stripped:
        return []

    # Separation of powers: an agent may request an amendment, never grant itself one.
    # If the agent could sign, the contract would only ever mean what the agent
    # currently wants it to mean.
    lowered = stripped.lower()
    if "northstar" in lowered and (" amend" in lowered or lowered.startswith("amend")):
        return [
            Judgement(
                Decision.DENY,
                "amendments are signed by the human, not by the agent; "
                "stop and state which grant you need",
                checks.Finding(checks.GOVERNANCE, "self_amend", "agent attempted to self-amend"),
            )
        ]

    hit = matches_any(stripped, contract.forbidden_commands)
    if hit is None:
        # Also match per-segment so `cd x && git push` is caught.
        for part in _split_command(stripped):
            hit = matches_any(part, contract.forbidden_commands)
            if hit is not None:
                break
    if hit is None:
        return []
    return [
        Judgement(
            Decision.DENY,
            f"command matches forbidden pattern `{hit}`",
            checks.Finding(checks.COMMAND, stripped, f"blocked by `{hit}`"),
        )
    ]


def _split_command(command: str) -> list[str]:
    parts = [command]
    for separator in ("&&", "||", ";", "|"):
        expanded: list[str] = []
        for part in parts:
            expanded.extend(part.split(separator))
        parts = expanded
    return [p.strip() for p in parts if p.strip()]


GateFn = Callable[[Contract, str, dict[str, Any], Path], Verdict]
