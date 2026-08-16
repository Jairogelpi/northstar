"""A deterministic, disposable tour of Northstar's complete decision loop."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import policy
from .authority import Authority
from .contract import default_contract
from .freeze import freeze

DEMO_SECRET = "northstar-disposable-demo-secret"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run() -> dict[str, Any]:
    """Exercise freeze, pre-write denial, state detection and scoped approval."""
    with tempfile.TemporaryDirectory(prefix="northstar-demo-") as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        _write(
            root,
            "src/auth.py",
            "def login(user: str, password: str) -> bool:\n    return bool(user and password)\n",
        )
        _write(root, "tests/test_auth.py", "from src.auth import login\n\ndef test_login():\n    assert login('a', 'b')\n")
        _write(root, "pyproject.toml", '[project]\nname = "demo"\ndependencies = []\n')

        contract = default_contract("refactor authentication without changing its public API")
        contract.constraints["protected_paths"] = ["tests/**"]
        oracle = freeze(root, contract.api_scope)
        authority = Authority.bootstrap(
            root,
            contract,
            oracle,
            home=Path(temporary) / "authority",
            approval_passphrase=DEMO_SECRET,
        )
        events: list[dict[str, Any]] = [
            {
                "step": "freeze",
                "decision": "ALLOW",
                "detail": f"frozen {len(oracle.files)} files and {len(oracle.api)} public symbols",
            }
        ]

        gate = policy.gate(contract, "Edit", {"file_path": "tests/test_auth.py"}, root)
        events.append(
            {
                "step": "protected test edit",
                "decision": gate.decision.value,
                "detail": gate.summary(),
            }
        )

        _write(
            root,
            "src/auth.py",
            "def login(user: str, password: str, tenant: str) -> bool:\n    return bool(user and password and tenant)\n",
        )
        drift = policy.evaluate(contract, oracle, root)
        finding = drift.blocking[0].finding
        assert finding is not None
        events.append(
            {
                "step": "whole-tree check",
                "decision": drift.decision.value,
                "detail": drift.summary(),
                "grant": finding.grant,
            }
        )

        request_id = authority.create_request("multi-tenant API approved for this task", [finding.grant])
        amendment = authority.approve_request(request_id, lambda request: DEMO_SECRET)
        contract, oracle = authority.load()
        final = policy.evaluate(contract, oracle, root)
        events.append(
            {
                "step": "scoped demo approval",
                "decision": final.decision.value,
                "detail": f"amendment v{amendment.version} authorises only {finding.grant}",
            }
        )
        return {
            "schema": 1,
            "objective": contract.objective,
            "disposable": True,
            "events": events,
            "final_decision": final.decision.value,
        }


def render(report: dict[str, Any]) -> str:
    lines = ["Northstar disposable demo", f"Objective: {report['objective']}", ""]
    for index, event in enumerate(report["events"], start=1):
        lines.append(f"{index}. {event['step']} -> {event['decision']}")
        lines.extend(f"   {line}" for line in str(event["detail"]).splitlines())
        if event.get("grant"):
            lines.append(f"   grant needed: {event['grant']}")
    lines.extend(["", "No user files were changed; the demo ran in a temporary checkout."])
    return "\n".join(lines)
