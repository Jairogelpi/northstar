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

import fnmatch
import re
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
    checks.TOOL: ("tools", "unknown"),
}

#: Tool names, across agents, that write to the filesystem.
WRITE_TOOLS = {"edit", "write", "notebookedit", "multiedit", "str_replace_editor", "apply_patch"}
SHELL_TOOLS = {"bash", "shell", "powershell", "run_command", "local_shell", "exec"}
READ_ONLY_TOOLS = {
    "read",
    "grep",
    "glob",
    "ls",
    "find",
    "websearch",
    "webfetch",
    "askuserquestion",
    "todowrite",
}


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
    """Verdict for one finding, honouring any authenticated amendment that covers it."""
    # Checked before any grant lookup: these refusals are of an action, not of a
    # state, so no signature can retroactively authorise them.
    if finding.kind in checks.NOT_AMENDABLE:
        return Judgement(Decision.DENY, finding.detail, finding)

    amendment = contract.is_granted(finding.kind, finding.identifier)
    if amendment is not None:
        return Judgement(
            Decision.ALLOW,
            f"{finding.detail} -- approved in amendment v{amendment.version}: {amendment.reason}",
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
    path_keys = {
        "file_path", "path", "notebook_path", "filepath", "target_file",
        "destination", "destination_path", "source_path", "filename",
    }

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str):
            if key in path_keys and value:
                out.append(value)
            if key in {"patch", "diff", "input", "command"}:
                out.extend(
                    match.group(1).strip()
                    for match in re.finditer(
                        r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$", value, re.MULTILINE
                    )
                )

    visit(params)
    return list(dict.fromkeys(out))


def _relative(path: str, root: Path, cwd: Path | None = None) -> str:
    candidate = Path(path)
    root = Path(root).resolve()
    working_directory = Path(cwd).resolve() if cwd is not None else root
    try:
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (working_directory / candidate).resolve()
        )
        return normalize(resolved.relative_to(root))
    except ValueError:
        return resolved.as_posix()


def _tool_matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _protected_path_judgements(
    contract: Contract, paths: list[str], root: Path, cwd: Path | None = None
) -> list[Judgement]:
    judgements: list[Judgement] = []
    try:
        from .authority import Authority

        authority_path = Authority.for_root(root).path.resolve()
    except (OSError, RuntimeError):  # pragma: no cover - defensive only
        authority_path = None
    for raw in paths:
        relative = _relative(raw, root, cwd)
        hit = matches_any(relative, contract.protected_paths)
        external_authority = False
        if authority_path is not None:
            try:
                Path(relative).resolve().relative_to(authority_path)
                external_authority = True
            except (OSError, ValueError):
                pass
        if hit is not None or external_authority:
            rule = hit or "external authority"
            judgements.append(
                Judgement(
                    Decision.DENY,
                    f"{relative} is protected by `{rule}`",
                    checks.Finding(checks.PROTECTED_PATH, relative, f"write blocked by `{rule}`"),
                )
            )
    return judgements


def gate(
    contract: Contract,
    tool: str,
    params: dict[str, Any],
    root: Path,
    cwd: Path | None = None,
) -> Verdict:
    """Pre-action gate: block on path and command alone, before anything is written."""
    name = (tool or "").strip().lower()
    judgements: list[Judgement] = []

    if name in SHELL_TOOLS:
        command = str(params.get("command") or params.get("cmd") or "")
        judgements.extend(_judge_command(contract, command, root))
        return Verdict(_worst([j.decision for j in judgements]), judgements)

    if name in READ_ONLY_TOOLS or _tool_matches(name, contract.read_only_tools):
        return Verdict(Decision.ALLOW, [])

    paths = _paths_from(params)
    if name in WRITE_TOOLS or _tool_matches(name, contract.mutating_tools):
        judgements.extend(_protected_path_judgements(contract, paths, root, cwd))
        if not paths:
            finding = checks.Finding(
                checks.TOOL,
                name or "missing_tool_name",
                "mutating tool supplied no inspectable target path",
            )
            judgements.append(judge(contract, finding))
        return Verdict(_worst([j.decision for j in judgements]), judgements)

    # An open-ended MCP/tool namespace cannot be safely modelled as a fixed list of
    # writers.  Unknown capabilities are therefore blocking until the contract
    # explicitly classifies the tool as read-only or mutating.
    judgements.extend(_protected_path_judgements(contract, paths, root, cwd))
    finding = checks.Finding(
        checks.TOOL,
        name or "missing_tool_name",
        "tool capability is unclassified; declare it read_only or mutating",
    )
    judgements.append(judge(contract, finding))

    return Verdict(_worst([j.decision for j in judgements]), judgements)


def _judge_command(contract: Contract, command: str, root: Path) -> list[Judgement]:
    stripped = command.strip()
    if not stripped:
        return []

    lowered = stripped.lower()
    normal = lowered.replace("\\", "/")
    protected_references = (
        ".northstar",
        ".claude/settings.json",
        ".codex/config.toml",
        ".codex/hooks.json",
        "agents.md",
        "claude.md",
    )
    try:
        from .authority import Authority

        protected_references = (*protected_references, Authority.for_root(root).path.as_posix().lower())
    except (OSError, RuntimeError):  # pragma: no cover - defensive only
        pass
    governance_command = re.search(
        r"(?:python\s+-m\s+)?northstar(?:\.cli)?\s+(?:approve|freeze|init|install|migrate)\b",
        normal,
    )
    direct_api = (
        "northstar.authority" in normal
        or (
            "northstar.contract" in normal
            and any(token in normal for token in (".amend", ".save", "contract("))
        )
        or (
            "from northstar import" in normal
            and any(token in normal for token in ("contract", "authority", "oracle"))
        )
    )
    if governance_command or direct_api or any(reference in normal for reference in protected_references):
        return [
            Judgement(
                Decision.DENY,
                "command targets Northstar authority, mirrors, wiring, or a human-only operation",
                checks.Finding(checks.GOVERNANCE, "authority_mutation", "agent attempted to alter governance state"),
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


GateFn = Callable[[Contract, str, dict[str, Any], Path, Path | None], Verdict]
