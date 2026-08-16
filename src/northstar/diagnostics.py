"""Read-only installation and runtime diagnostics.

``northstar doctor`` must be safe to run when governance is healthy, partially
installed, or broken.  It never repairs state and never records a journal entry;
the output is evidence about the installation, not another action to trust.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__, evidence, policy
from .authority import Authority, IntegrityError
from .install import integrity_issues


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _configured_agents(wiring: list[str]) -> set[str]:
    agents: set[str] = set()
    normal = {path.replace("\\", "/") for path in wiring}
    if ".claude/settings.json" in normal or "CLAUDE.md" in normal:
        agents.add("claude")
    if ".codex/hooks.json" in normal or "AGENTS.md" in normal:
        agents.add("codex")
    return agents


def _overall(checks: list[Check]) -> str:
    if any(check.status == "fail" for check in checks):
        return "broken"
    if any(check.status == "warn" for check in checks):
        return "degraded"
    return "healthy"


def run(root: Path) -> dict[str, Any]:
    """Inspect one checkout without mutating it."""
    root = Path(root).resolve()
    checks = [Check("runtime", "pass", f"northstar-runtime {__version__} on Python {sys.version.split()[0]}")]
    authority: Authority | None = None
    contract = oracle = None

    try:
        authority = Authority.open(root)
    except IntegrityError as exc:
        checks.append(
            Check(
                "authority",
                "fail",
                str(exc),
                "Restore the external authority or stop agents before re-initialising.",
            )
        )

    if authority is None and not any(check.name == "authority" for check in checks):
        checks.append(
            Check(
                "authority",
                "warn",
                "project is not governed",
                "Run `northstar init` from a human terminal.",
            )
        )
    elif authority is not None:
        try:
            contract, oracle = authority.load(check_wiring=False)
            checks.append(Check("authority", "pass", f"sealed authority verifies at {authority.path}"))
        except IntegrityError as exc:
            checks.append(Check("authority", "fail", str(exc), "Stop agents and repair the trusted authority."))

    wiring: list[str] = []
    if authority is not None:
        try:
            wiring = [str(path) for path in authority.metadata().get("wiring", [])]
        except IntegrityError:
            wiring = []

    if authority is not None and contract is not None and oracle is not None:
        issues = integrity_issues(root, wiring)
        checks.append(
            Check(
                "wiring",
                "fail" if issues else "pass",
                "; ".join(issues) if issues else f"{len(wiring)} sealed integration file(s) verify",
                "Run `northstar install` from a human terminal." if issues else None,
            )
        )
        verdict = policy.evaluate(contract, oracle, root)
        checks.append(
            Check(
                "tree",
                "fail" if verdict.is_blocking else "pass",
                verdict.summary(),
                "Resolve the reported divergence before continuing." if verdict.is_blocking else None,
            )
        )
        try:
            entries = evidence.read_journal(root)
        except IntegrityError as exc:
            entries = []
            checks.append(Check("hook_activity", "fail", str(exc)))
        else:
            hook_entries = [entry for entry in entries if entry.phase in {"gate", "check"}]
            checks.append(
                Check(
                    "hook_activity",
                    "pass" if hook_entries else "warn",
                    f"{len(hook_entries)} hook decision(s) recorded"
                    if hook_entries
                    else "no hook activity has been observed yet",
                    "Run one agent action, then repeat `northstar doctor`." if not hook_entries else None,
                )
            )

    agents = _configured_agents(wiring)
    for agent in sorted(agents):
        executable = shutil.which(agent)
        checks.append(
            Check(
                f"agent:{agent}",
                "pass" if executable else "warn",
                f"found {executable}" if executable else f"{agent} executable is not on PATH",
                f"Install {agent} or uninstall its Northstar adapter." if not executable else None,
            )
        )
    if "codex" in agents:
        checks.append(
            Check(
                "codex_hook_trust",
                "warn",
                "project hook trust is user-local and cannot be inspected by Northstar",
                "Open `/hooks` in Codex, review the root-bound command, and trust it.",
            )
        )

    return {
        "schema": 1,
        "root": str(root),
        "overall": _overall(checks),
        "governed": authority is not None,
        "checks": [check.to_dict() for check in checks],
    }


def render(report: dict[str, Any]) -> str:
    icons = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = [f"northstar doctor: {str(report['overall']).upper()}", f"  root: {report['root']}"]
    for check in report["checks"]:
        lines.append(f"  [{icons[check['status']]}] {check['name']}: {check['detail']}")
        if check.get("remediation"):
            lines.append(f"         next: {check['remediation']}")
    return "\n".join(lines)
