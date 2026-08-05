"""Runtime adapters.

One normaliser, several agents. Claude Code and Codex disagree about JSON key
names and about which event fires when, but every one of them ultimately says
"this tool, these arguments" -- so the policy never learns their dialects.

The hook process is deliberately separate from the agent: it verifies and re-reads
the external authority on every call. The contract is never remembered, which makes
it immune to context compaction, summarisation and working-tree mirror tampering.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from . import checks, evidence, policy
from .authority import Authority, IntegrityError
from .contract import Contract
from .policy import Decision, Verdict

#: Claude Code treats exit code 2 as "block and show stderr to the model".
EXIT_BLOCK = 2
EXIT_OK = 0

_TOOL_KEYS = ("tool_name", "tool", "name", "toolName")
_PARAM_KEYS = ("tool_input", "arguments", "input", "params", "parameters", "toolInput")
_EVENT_KEYS = ("hook_event_name", "event", "phase", "hook")


@dataclass
class HookEvent:
    tool: str
    params: dict[str, Any]
    event: str
    cwd: str | None

    @property
    def is_post(self) -> bool:
        return "post" in self.event.lower()


def parse_event(payload: dict[str, Any]) -> HookEvent:
    """Normalise a hook payload from any supported agent."""
    tool = ""
    for key in _TOOL_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            tool = value
            break
    params: dict[str, Any] = {}
    for key in _PARAM_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            params = value
            break
    event = ""
    for key in _EVENT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            event = value
            break
    cwd = payload.get("cwd") or payload.get("workspace") or payload.get("project_dir")
    return HookEvent(tool=tool, params=params, event=event, cwd=cwd if isinstance(cwd, str) else None)


def read_payload(stream: TextIO) -> dict[str, Any]:
    """Tolerant read: an unreadable payload must never wedge the agent."""
    try:
        raw = stream.read()
    except (OSError, ValueError):  # pragma: no cover - stream teardown
        return {}
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def block_message(verdict: Verdict, contract: Contract) -> str:
    """What the agent is told when it is stopped.

    A block is not a wall, it is a question with evidence attached. So the message
    names the divergence, names the grant that would authorise it, and says who
    can approve -- otherwise the agent guesses, and guessing is how drift restarts.
    """
    lines = ["NORTHSTAR: this action diverges from the intent contract.", ""]
    lines.append(f'Objective (contract v{contract.version}): "{contract.objective}"')
    lines.append("")
    amendable = [
        j.finding
        for j in verdict.blocking
        if j.finding is not None and j.finding.kind not in checks.NOT_AMENDABLE
    ]
    for judgement in verdict.blocking:
        finding = judgement.finding
        label = finding.kind if finding else "gate"
        lines.append(f"  [{judgement.decision.value}] {label}: {judgement.reason}")
        if finding is not None and finding in amendable:
            lines.append(f"      grant needed: {finding.grant}")
    lines.append("")
    if amendable:
        lines.append(
            "Do not work around this and do not edit .northstar/. Either take another "
            "route, or create a request for a human to approve:"
        )
        for finding in amendable:
            lines.append(f'    northstar request --grant "{finding.grant}" --reason "..."')
    else:
        # Nothing here is grantable, so offering a command would only invite a retry
        # of what was just refused.
        lines.append("This refusal is not amendable. Take another route.")
    return "\n".join(lines)


def handle(
    payload: dict[str, Any],
    root: Path,
    stderr: TextIO | None = None,
) -> int:
    """Run the right check for the event and return the process exit code."""
    stderr = stderr if stderr is not None else sys.stderr
    event = parse_event(payload)
    try:
        authority = Authority.open(root)
        if authority is None:
            if (Path(root) / ".northstar" / "contract.yaml").exists():
                raise IntegrityError("legacy in-tree authority is not trusted; re-initialise with R1")
            return EXIT_OK  # genuinely ungoverned project; stay out of the way
        contract, oracle = authority.load()
    except IntegrityError as exc:
        stderr.write(
            "NORTHSTAR [DENY]\n"
            f"[INTEGRITY_FAILURE] {exc}\n"
            "Governance state is missing or tampered. Stop; a human must repair it.\n"
        )
        return EXIT_BLOCK

    # Hook payloads may report a nested working directory. Runtime state and
    # post-state checks always use the checkout root discovered by the authority;
    # relative tool paths are still resolved from the action's actual cwd.
    action_cwd = Path(event.cwd).resolve() if event.cwd else Path(root).resolve()
    root = authority.root

    if event.is_post:
        verdict = policy.evaluate(contract, oracle, root)
        evidence.record(
            root,
            "check",
            event.tool,
            verdict,
            replay=evidence.capture_replay(root, oracle, event.params),
        )
    else:
        verdict = policy.gate(contract, event.tool, event.params, root, cwd=action_cwd)
        if verdict.judgements:
            evidence.record(root, "gate", event.tool, verdict)

    if verdict.is_blocking:
        stderr.write(block_message(verdict, contract) + "\n")
        return EXIT_BLOCK
    if verdict.decision in (Decision.WARN_DRIFT, Decision.UNKNOWN):
        stderr.write(f"NORTHSTAR [{verdict.decision.value}]\n{verdict.summary()}\n")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    from .util import find_root

    payload = read_payload(sys.stdin)
    root = Path(payload.get("cwd") or find_root())
    return handle(payload, root)
