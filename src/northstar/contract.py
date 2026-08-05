"""The intent contract: a small, human-written deny-list of what must not change.

The human declares little. The freezer derives much. Anything the contract does
not name is free -- the contract is never a specification of the whole project,
so there is no waterfall and no penalty for a short contract.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .util import normalize, state_dir

FORBIDDEN = "forbidden"
APPROVAL_REQUIRED = "approval_required"
ALLOWED = "allowed"
RULES = (FORBIDDEN, APPROVAL_REQUIRED, ALLOWED)

CONTRACT_FILE = "contract.yaml"

#: Working-tree mirrors and wiring an agent may never write, whatever the contract
#: says. The external authority verifies these independently and fails closed.
HARD_PROTECTED = [
    ".northstar/**",
    ".claude/settings.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    "AGENTS.md",
    "CLAUDE.md",
]

DEFAULT_CONSTRAINTS: dict[str, Any] = {
    "protected_paths": [],
    "public_api": {"change": FORBIDDEN, "additions": ALLOWED, "scope": ["**/*.py"]},
    "dependencies": {"additions": FORBIDDEN},
    "module_graph": {"new_edges": ALLOWED},
    # Off by default: capturing it costs a full test run at freeze time, and a
    # constraint that makes `init` slow is a constraint people turn off.
    "behavior": {"change": ALLOWED, "command": []},
    "scope": {"max_files": 0, "max_lines": 0},
    "commands": {"forbidden": []},
    # Capability-first: tools not positively classified are blocking by default.
    # MCP names are open-ended, so a fixed write-tool list cannot be a boundary.
    "tools": {"unknown": APPROVAL_REQUIRED, "read_only": [], "mutating": []},
}


class ContractError(Exception):
    """Contract is malformed. Refuse to run rather than enforce a guess."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Amendment:
    """A scoped widening authenticated by the external authority.

    Signing re-baselines only the named grants; every other invariant stays frozen
    against the original baseline. Without that, one exception would read as a
    general amnesty.
    """

    version: int
    reason: str
    grants: list[str]
    signed_by: str = "human"
    approval_id: str | None = None
    signature: str | None = None
    at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "at": self.at,
            "reason": self.reason,
            "signed_by": self.signed_by,
            "approval_id": self.approval_id,
            "signature": self.signature,
            "grants": list(self.grants),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Amendment":
        try:
            return Amendment(
                version=int(data["version"]),
                reason=str(data["reason"]),
                grants=[str(g) for g in data.get("grants", [])],
                signed_by=str(data.get("signed_by", "human")),
                approval_id=str(data["approval_id"]) if data.get("approval_id") else None,
                signature=str(data["signature"]) if data.get("signature") else None,
                at=str(data.get("at", _utcnow())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"invalid amendment: {data!r}") from exc


@dataclass
class Contract:
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    amendments: list[Amendment] = field(default_factory=list)
    created: str = field(default_factory=_utcnow)

    # -- lifecycle ---------------------------------------------------------

    def __post_init__(self) -> None:
        merged = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULT_CONSTRAINTS.items()}
        for key, value in (self.constraints or {}).items():
            if key not in DEFAULT_CONSTRAINTS:
                raise ContractError(f"unknown constraint section: {key!r}")
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
        self.constraints = merged
        self._validate()

    def _validate(self) -> None:
        if not self.objective or not str(self.objective).strip():
            raise ContractError("contract needs an objective")
        for section, key in (
            ("public_api", "change"),
            ("public_api", "additions"),
            ("dependencies", "additions"),
            ("module_graph", "new_edges"),
            ("behavior", "change"),
            ("tools", "unknown"),
        ):
            value = self.constraints[section].get(key)
            if value not in RULES:
                raise ContractError(f"{section}.{key} must be one of {RULES}, got {value!r}")
        for key in ("max_files", "max_lines"):
            value = self.constraints["scope"][key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractError(f"scope.{key} must be a non-negative int, got {value!r}")
        if not isinstance(self.constraints["protected_paths"], list):
            raise ContractError("protected_paths must be a list of globs")
        for key in ("read_only", "mutating"):
            if not isinstance(self.constraints["tools"].get(key), list):
                raise ContractError(f"tools.{key} must be a list of tool-name globs")

    @property
    def version(self) -> int:
        """v1 at creation; every approved amendment bumps it."""
        return 1 + len(self.amendments)

    # -- queries -----------------------------------------------------------

    @property
    def protected_paths(self) -> list[str]:
        declared = [normalize(p) for p in self.constraints["protected_paths"]]
        return [*HARD_PROTECTED, *declared]

    @property
    def forbidden_commands(self) -> list[str]:
        return [str(c) for c in self.constraints["commands"].get("forbidden", [])]

    @property
    def tracks_behavior(self) -> bool:
        return self.rule("behavior", "change") != ALLOWED

    @property
    def behavior_command(self) -> list[str]:
        return [str(part) for part in self.constraints["behavior"].get("command", [])]

    @property
    def api_scope(self) -> list[str]:
        return [normalize(p) for p in self.constraints["public_api"].get("scope", [])]

    @property
    def read_only_tools(self) -> list[str]:
        return [str(name).lower() for name in self.constraints["tools"].get("read_only", [])]

    @property
    def mutating_tools(self) -> list[str]:
        return [str(name).lower() for name in self.constraints["tools"].get("mutating", [])]

    def rule(self, section: str, key: str) -> str:
        return str(self.constraints[section][key])

    def is_granted(self, kind: str, identifier: str) -> Amendment | None:
        """Has an approved amendment already authorised this exact divergence?

        Grants are `kind:identifier` and may glob, so a human can approve
        `public_api:src/auth.py::*` without reopening the whole API surface.
        """
        target = f"{kind}:{identifier}"
        for amendment in self.amendments:
            for grant in amendment.grants:
                if grant == target or fnmatch.fnmatch(target, grant):
                    return amendment
        return None

    def amend(
        self,
        reason: str,
        grants: list[str],
        signed_by: str = "human",
        approval_id: str | None = None,
    ) -> Amendment:
        if not reason.strip():
            raise ContractError("an amendment needs a reason")
        if not grants:
            raise ContractError("an amendment needs at least one grant")
        amendment = Amendment(
            version=self.version + 1,
            reason=reason,
            grants=list(grants),
            signed_by=signed_by,
            approval_id=approval_id,
        )
        self.amendments.append(amendment)
        return amendment

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "objective": self.objective,
            "created": self.created,
            "constraints": self.constraints,
            "amendments": [a.to_dict() for a in self.amendments],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Contract":
        if not isinstance(data, dict):
            raise ContractError("contract must be a YAML mapping")
        return Contract(
            objective=str(data.get("objective", "")),
            constraints=data.get("constraints") or {},
            amendments=[Amendment.from_dict(a) for a in data.get("amendments") or []],
            created=str(data.get("created", _utcnow())),
        )

    def save(self, root: Path) -> Path:
        target = contract_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return target

    @staticmethod
    def load(root: Path) -> "Contract":
        path = contract_path(root)
        if not path.exists():
            raise ContractError(f"no contract at {path}; run `northstar init` first")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ContractError(f"contract is not valid YAML: {exc}") from exc
        return Contract.from_dict(data)


def contract_path(root: Path) -> Path:
    return state_dir(root) / CONTRACT_FILE


TEMPLATE = """\
# Northstar intent contract -- a deny-list, not a specification.
# Everything not named here is free. Short contracts are fine.
objective: {objective}

constraints:
  # Files the agent must never touch. Northstar mirrors and wiring are always protected.
  protected_paths:
    - tests/**

  public_api:
    change: forbidden        # forbidden | approval_required | allowed
    additions: allowed
    scope:
      - "**/*.py"

  dependencies:
    additions: forbidden

  module_graph:
    new_edges: allowed

  scope:
    max_files: 0             # 0 = no budget
    max_lines: 0

  commands:
    forbidden:
      - "git push*"
      - "rm -rf*"

  # Unknown/MCP tools are blocked pending approval unless classified here.
  tools:
    unknown: approval_required
    read_only: []
    mutating: []
"""


def default_contract(objective: str) -> Contract:
    return Contract.from_dict(yaml.safe_load(TEMPLATE.format(objective=objective)))
