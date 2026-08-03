"""The journal and the receipt.

Every decision is appended to a journal; the receipt binds contract, baseline,
final tree and every signed amendment into one auditable object. The chain of
amendments is the point: at the end you can see not only what was built, but
where the original intent turned out to be wrong and who decided that.

The receipt also carries the metric that actually matters over long runs:
`wasted_steps` -- how many steps ran between a violation first appearing and it
being detected. Blocking at step 50 saves correctness but burns 40 steps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import Contract
from .freeze import Oracle
from .policy import Decision, Verdict
from .util import read_text, state_dir

JOURNAL_FILE = "journal.jsonl"
RECEIPT_FILE = "receipt.json"


def journal_path(root: Path) -> Path:
    return state_dir(root) / JOURNAL_FILE


def receipt_path(root: Path) -> Path:
    return state_dir(root) / RECEIPT_FILE


@dataclass
class Entry:
    step: int
    at: str
    phase: str  # "gate" | "check" | "amend"
    tool: str
    decision: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "at": self.at,
            "phase": self.phase,
            "tool": self.tool,
            "decision": self.decision,
            "detail": self.detail,
        }


def read_journal(root: Path) -> list[Entry]:
    path = journal_path(root)
    if not path.exists():
        return []
    entries: list[Entry] = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue  # a corrupt line must not blind the rest of the journal
        entries.append(
            Entry(
                step=int(data.get("step", 0)),
                at=str(data.get("at", "")),
                phase=str(data.get("phase", "")),
                tool=str(data.get("tool", "")),
                decision=str(data.get("decision", "")),
                detail=data.get("detail", {}),
            )
        )
    return entries


def next_step(root: Path) -> int:
    entries = read_journal(root)
    return (entries[-1].step + 1) if entries else 1


def record(root: Path, phase: str, tool: str, verdict: Verdict) -> Entry:
    entry = Entry(
        step=next_step(root),
        at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        phase=phase,
        tool=tool,
        decision=verdict.decision.value,
        detail=verdict.to_dict(),
    )
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
    return entry


def record_amendment(root: Path, reason: str, grants: list[str], version: int) -> Entry:
    entry = Entry(
        step=next_step(root),
        at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        phase="amend",
        tool="human",
        decision="SIGNED",
        detail={"version": version, "reason": reason, "grants": grants},
    )
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
    return entry


def wasted_steps(entries: list[Entry]) -> int:
    """Steps between the first non-clean verdict and the first blocking one.

    If the run never blocked, the whole tail after the first warning counts: work
    kept flowing while the trajectory was already off course.
    """
    first_divergence: int | None = None
    for entry in entries:
        if entry.phase == "amend":
            continue
        if entry.decision != Decision.ALLOW.value and first_divergence is None:
            first_divergence = entry.step
        if entry.decision in (Decision.DENY.value, Decision.REQUIRE_APPROVAL.value):
            return entry.step - (first_divergence or entry.step)
    if first_divergence is None:
        return 0
    return entries[-1].step - first_divergence


def build_receipt(root: Path, contract: Contract, oracle: Oracle, verdict: Verdict) -> dict[str, Any]:
    entries = read_journal(root)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.decision] = counts.get(entry.decision, 0) + 1
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "objective": contract.objective,
        "contract_version": contract.version,
        "base_commit": oracle.base_commit,
        "baseline_created": oracle.created,
        "final_verdict": verdict.to_dict(),
        "amendments": [a.to_dict() for a in contract.amendments],
        "uncovered_files": oracle.unknown,
        "metrics": {
            "steps": entries[-1].step if entries else 0,
            "decisions": counts,
            "wasted_steps": wasted_steps(entries),
        },
    }


def write_receipt(root: Path, receipt: dict[str, Any]) -> Path:
    path = receipt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return path
