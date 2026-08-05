"""Deterministic checks: current tree versus the frozen oracle.

Every check compares against the *baseline*, never against the previous step.
That makes violations monotone: no sequence of intermediate edits can launder a
divergence, and an agent that breaks the API at step 12 and "fixes" it at step 40
is still caught at both. Per-delta guardrails miss exactly this, because drift
does not live in any single edit -- it lives in the trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import behavior, surface
from .contract import Contract
from .freeze import (
    Oracle,
    extract_dependencies,
    extract_imports,
    module_name,
)
from .util import (
    count_lines,
    hash_text,
    iter_source_files,
    matches_any,
    normalize,
    read_text,
)

# Finding kinds double as the namespace of amendment grants: `kind:identifier`.
PROTECTED_PATH = "protected_path"
PUBLIC_API = "public_api"
API_ADDITION = "public_api_addition"
DEPENDENCY = "dependency"
MODULE_EDGE = "module_edge"
SCOPE = "scope"
BEHAVIOR = "behavior"
UNKNOWN_KIND = "unknown"

#: Gate-only kinds. They are refusals of an *action*, not divergences of state, so
#: no amendment can grant them -- suggesting one would invite the agent to retry
#: the exact thing it was just refused.
COMMAND = "command"
GOVERNANCE = "governance"
INTEGRITY = "integrity_failure"
TOOL = "unclassified_tool"
NOT_AMENDABLE = (COMMAND, GOVERNANCE, INTEGRITY)


@dataclass
class Finding:
    """One divergence from the baseline. Not yet a verdict -- policy decides that."""

    kind: str
    identifier: str
    detail: str

    @property
    def grant(self) -> str:
        return f"{self.kind}:{self.identifier}"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "identifier": self.identifier, "detail": self.detail}


@dataclass
class TreeState:
    """Current working tree, read once and shared by every check."""

    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    api: dict[str, str] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    module_graph: dict[str, list[str]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    fidelity: dict[str, str] = field(default_factory=dict)
    root: Path = field(default_factory=Path)


def read_tree(root: Path, api_scope: list[str]) -> TreeState:
    root = Path(root)
    state = TreeState(root=root)
    sources: dict[str, str] = {}
    for relative in iter_source_files(root):
        key = normalize(relative)
        text = read_text(root / relative)
        sources[key] = text
        state.files[key] = {"hash": hash_text(text), "lines": count_lines(text)}

    python_files = {k: v for k, v in sources.items() if k.endswith(".py")}
    known = {module_name(k) for k in python_files} - {""}
    for key, text in python_files.items():
        try:
            imports = extract_imports(text)
        except SyntaxError:
            continue  # recorded as UNKNOWN by the surface pass below
        source_module = module_name(key)
        edges = sorted(
            {
                resolved
                for resolved in (_resolve(i, known) for i in imports)
                if resolved and resolved != source_module
            }
        )
        if edges:
            state.module_graph[source_module] = edges

    for key, text in sources.items():
        if matches_any(key, api_scope) is None or key.endswith(surface.NO_SURFACE):
            continue
        try:
            found = surface.extract(text, key)
        except SyntaxError:
            state.unknown.append(key)
            continue
        if found is None:
            state.unknown.append(key)
            continue
        state.api.update(found.symbols)
        state.fidelity[key] = found.fidelity

    state.dependencies = extract_dependencies(root)
    state.unknown = sorted(set(state.unknown))
    return state


def _resolve(imported: str, known: set[str]) -> str | None:
    parts = imported.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in known:
            return candidate
    return None


# ----------------------------------------------------------------- the checks


def check_protected_paths(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    patterns = contract.protected_paths
    findings: list[Finding] = []
    for path, before in oracle.files.items():
        if matches_any(path, patterns) is None:
            continue
        after = state.files.get(path)
        if after is None:
            findings.append(Finding(PROTECTED_PATH, path, "protected file deleted"))
        elif after["hash"] != before["hash"]:
            findings.append(Finding(PROTECTED_PATH, path, "protected file modified"))
    for path in state.files:
        if path not in oracle.files and matches_any(path, patterns) is not None:
            findings.append(Finding(PROTECTED_PATH, path, "file created under a protected path"))
    return findings


def check_public_api(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    findings: list[Finding] = []

    def qualify(symbol: str, detail: str) -> str:
        """Name the extractor's ceiling on the finding itself, not in a footnote."""
        path = symbol.split("::", 1)[0]
        if oracle.fidelity.get(path, state.fidelity.get(path)) == surface.HEURISTIC:
            return f"{detail} (heuristic extractor: pattern-based, may miss exotic declarations)"
        return detail

    for symbol, signature in oracle.api.items():
        current = state.api.get(symbol)
        if current is None:
            findings.append(Finding(PUBLIC_API, symbol, qualify(symbol, "public symbol removed")))
        elif current != signature:
            findings.append(
                Finding(
                    PUBLIC_API,
                    symbol,
                    qualify(symbol, f"signature changed: {signature} -> {current}"),
                )
            )
    for symbol in state.api:
        if symbol not in oracle.api:
            findings.append(Finding(API_ADDITION, symbol, "public symbol added"))
    return findings


def check_dependencies(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    findings: list[Finding] = []
    for manifest, current in state.dependencies.items():
        before = set(oracle.dependencies.get(manifest, []))
        for name in sorted(set(current) - before):
            findings.append(Finding(DEPENDENCY, name, f"runtime dependency added in {manifest}"))
    return findings


def check_module_graph(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    findings: list[Finding] = []
    for source, targets in state.module_graph.items():
        before = set(oracle.module_graph.get(source, []))
        for target in sorted(set(targets) - before):
            findings.append(
                Finding(MODULE_EDGE, f"{source}->{target}", "new dependency between modules")
            )
    return findings


def check_scope(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    """Budget on how far the change may spread. 0 means no budget."""
    max_files = int(contract.constraints["scope"]["max_files"])
    max_lines = int(contract.constraints["scope"]["max_lines"])
    changed, churn = changed_files(oracle, state)
    findings: list[Finding] = []
    if max_files and len(changed) > max_files:
        findings.append(
            Finding(SCOPE, "max_files", f"{len(changed)} files changed, budget is {max_files}")
        )
    if max_lines and churn > max_lines:
        findings.append(
            Finding(SCOPE, "max_lines", f"~{churn} lines changed, budget is {max_lines}")
        )
    return findings


def changed_files(oracle: Oracle, state: TreeState) -> tuple[list[str], int]:
    """Paths touched since the baseline, plus an approximate line churn.

    ponytail: churn is |lines_now - lines_then| per file, not a real diff. Cheap,
    monotone enough for a budget signal. Swap in a line-level diff if the budget
    ever needs to be exact.
    """
    changed: list[str] = []
    churn = 0
    for path, before in oracle.files.items():
        after = state.files.get(path)
        if after is None:
            changed.append(path)
            churn += int(before["lines"])
        elif after["hash"] != before["hash"]:
            changed.append(path)
            churn += abs(int(after["lines"]) - int(before["lines"])) or 1
    for path, after in state.files.items():
        if path not in oracle.files:
            changed.append(path)
            churn += int(after["lines"])
    return sorted(set(changed)), churn


def check_unknown(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    """Files the freezer could not read.

    UNKNOWN is a first-class outcome. A file that cannot be parsed is not a file
    that is fine -- claiming coverage we do not have is the failure mode that
    makes a guardrail worse than none.
    """
    return [
        Finding(UNKNOWN_KIND, path, "file could not be parsed; not covered by the oracle")
        for path in sorted(set(state.unknown) - set(oracle.unknown))
    ]


def check_behavior(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    """Compare today's test outcomes against the ones frozen at t=0.

    Skipped entirely unless the contract asks for it -- re-running a suite on every
    check is expensive, and a check people disable is worse than one they opt into.
    """
    if not contract.tracks_behavior or not oracle.behavior:
        return []
    run = behavior.capture(state.root, oracle.behavior_command or contract.behavior_command or None)
    if not run.usable:
        return [
            Finding(
                UNKNOWN_KIND,
                "behavior",
                f"behavioural oracle could not be re-run ({run.error}); "
                "behaviour is unverified, not unchanged",
            )
        ]
    return [
        Finding(BEHAVIOR, name, f"test outcome changed: {before} -> {after}")
        for name, before, after in behavior.compare(oracle.behavior, run.outcomes)
    ]


ALL_CHECKS = (
    check_protected_paths,
    check_public_api,
    check_dependencies,
    check_module_graph,
    check_scope,
    check_behavior,
    check_unknown,
)


def run_all(contract: Contract, oracle: Oracle, state: TreeState) -> list[Finding]:
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(contract, oracle, state))
    return findings
