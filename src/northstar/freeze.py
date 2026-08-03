"""The freezer: turn the repo as it stands at t=0 into a deterministic oracle.

Intent is sharpest at the start -- clean context, human present. So the expensive
judgement happens once, here, and is frozen into artifacts a dumb process can
re-check at step 500. Nothing in this module needs an LLM, and nothing in it
looks at the agent's conversation.

Every derived fact comes from the repository, not from the human's imagination.
That is what stops the contract from becoming a waterfall spec.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import behavior, surface
from .util import (
    count_lines,
    git_commit,
    hash_text,
    iter_source_files,
    matches_any,
    normalize,
    read_text,
    state_dir,
)

ORACLE_FILE = "oracle.json"

DEP_SOURCES = ("pyproject.toml", "requirements.txt", "package.json")
_DEP_SPLIT = re.compile(r"[<>=!~\[;@ ]")


# ---------------------------------------------------------------- api surface


def extract_api(source: str, relpath: str) -> dict[str, str]:
    """Public API symbols for a file, whatever its language.

    Raises SyntaxError so the caller can record the file as UNKNOWN instead of
    silently claiming an empty -- and therefore always-satisfied -- API surface.
    """
    result = surface.extract(source, relpath)
    return result.symbols if result is not None else {}


# ---------------------------------------------------------------- dependencies


def _clean_dep(raw: str) -> str:
    return _DEP_SPLIT.split(raw.strip(), 1)[0].strip().lower()


def extract_dependencies(root: Path) -> dict[str, list[str]]:
    """Declared runtime dependencies per manifest. Dev/test extras are ignored."""
    root = Path(root)
    found: dict[str, list[str]] = {}

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(read_text(pyproject))
        except tomllib.TOMLDecodeError:
            data = {}
        deps = data.get("project", {}).get("dependencies", []) or []
        found["pyproject.toml"] = sorted({_clean_dep(d) for d in deps if _clean_dep(d)})

    requirements = root / "requirements.txt"
    if requirements.exists():
        names = set()
        for line in read_text(requirements).splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                names.add(_clean_dep(line))
        found["requirements.txt"] = sorted(n for n in names if n)

    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(read_text(package))
        except json.JSONDecodeError:
            data = {}
        deps = data.get("dependencies", {}) or {}
        found["package.json"] = sorted(str(k).lower() for k in deps)

    return found


# --------------------------------------------------------------- module graph


def module_name(relpath: str) -> str:
    """Dotted module name for a repo-relative python file."""
    parts = normalize(relpath)[: -len(".py")].split("/")
    if parts and parts[0] in ("src", "lib"):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def extract_imports(source: str) -> set[str]:
    """Imported dotted names, absolute form only.

    Relative imports are dropped: they cannot leave the package, so they can never
    create the cross-module edge this check exists to catch.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _resolve(imported: str, known: set[str]) -> str | None:
    """Longest known module that the import prefixes onto, else None (external)."""
    parts = imported.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in known:
            return candidate
    return None


# --------------------------------------------------------------------- oracle


@dataclass
class Oracle:
    """Frozen witness of the repository at the moment the contract was signed."""

    created: str
    base_commit: str | None
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    api: dict[str, str] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    module_graph: dict[str, list[str]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    #: path -> "ast" | "heuristic". How much the surface for that file can be trusted.
    fidelity: dict[str, str] = field(default_factory=dict)
    #: name -> outcome, frozen from the baseline test run (see behavior.py).
    behavior: dict[str, str] = field(default_factory=dict)
    behavior_command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "base_commit": self.base_commit,
            "files": self.files,
            "api": self.api,
            "dependencies": self.dependencies,
            "module_graph": self.module_graph,
            "unknown": self.unknown,
            "fidelity": self.fidelity,
            "behavior": self.behavior,
            "behavior_command": self.behavior_command,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Oracle":
        return Oracle(
            created=str(data.get("created", "")),
            base_commit=data.get("base_commit"),
            files=data.get("files", {}),
            api=data.get("api", {}),
            dependencies=data.get("dependencies", {}),
            module_graph=data.get("module_graph", {}),
            unknown=data.get("unknown", []),
            fidelity=data.get("fidelity", {}),
            behavior=data.get("behavior", {}),
            behavior_command=data.get("behavior_command", []),
        )

    def save(self, root: Path) -> Path:
        path = oracle_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @staticmethod
    def load(root: Path) -> "Oracle":
        path = oracle_path(root)
        if not path.exists():
            raise FileNotFoundError(f"no oracle at {path}; run `northstar freeze` first")
        return Oracle.from_dict(json.loads(read_text(path)))


def oracle_path(root: Path) -> Path:
    return state_dir(root) / ORACLE_FILE


def freeze(
    root: Path,
    api_scope: list[str] | None = None,
    capture_behavior: bool = False,
    behavior_command: list[str] | None = None,
) -> Oracle:
    """Build the oracle from the current working tree.

    Deliberately snapshot-based rather than git-based: a project need not be a git
    repo for its intent to be worth protecting, and a snapshot cannot be rewritten
    by a rebase.
    """
    root = Path(root)
    scope = api_scope if api_scope is not None else ["**/*.py"]
    oracle = Oracle(
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        base_commit=git_commit(root),
    )

    sources: dict[str, str] = {}
    for relative in iter_source_files(root):
        key = normalize(relative)
        text = read_text(root / relative)
        sources[key] = text
        oracle.files[key] = {"hash": hash_text(text), "lines": count_lines(text)}

    python_files = {k: v for k, v in sources.items() if k.endswith(".py")}
    known_modules = {module_name(k) for k in python_files} - {""}

    for key, text in python_files.items():
        try:
            imports = extract_imports(text)
        except SyntaxError:
            continue  # recorded as UNKNOWN by the surface pass below
        source_module = module_name(key)
        edges = sorted(
            {
                target
                for target in (_resolve(i, known_modules) for i in imports)
                if target and target != source_module
            }
        )
        if edges:
            oracle.module_graph[source_module] = edges

    for key, text in sources.items():
        if matches_any(key, scope) is None or key.endswith(surface.NO_SURFACE):
            continue
        try:
            found = surface.extract(text, key)
        except SyntaxError:
            oracle.unknown.append(key)
            continue
        if found is None:
            # No extractor for this language. Saying so beats freezing an empty
            # surface that can never be violated.
            oracle.unknown.append(key)
            continue
        oracle.api.update(found.symbols)
        oracle.fidelity[key] = found.fidelity

    oracle.dependencies = extract_dependencies(root)

    if capture_behavior:
        run = behavior.capture(root, behavior_command or None)
        oracle.behavior = run.outcomes
        oracle.behavior_command = run.command
        if not run.usable:
            # The baseline could not be witnessed. Say so loudly: a behaviour
            # oracle that silently captured nothing would pass forever.
            oracle.unknown.append(f"behavior: {run.error}")

    oracle.unknown = sorted(set(oracle.unknown))
    return oracle
