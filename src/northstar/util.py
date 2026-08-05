"""Filesystem + hashing helpers. No git required: the oracle carries its own baseline."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from pathlib import Path

STATE_DIR = ".northstar"

#: Never walked, never hashed, never editable by an agent.
ALWAYS_IGNORED = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".tox",
    "dist",
    "build",
    ".idea",
    ".vscode",
    STATE_DIR,
    ".northstar-authority",
    ".northstar-bench-authority",
)

SOURCE_SUFFIXES = (
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".toml",
    ".cfg",
    ".ini",
    ".json",
    ".yaml",
    ".yml",
    ".sql",
    ".md",
    ".txt",
)


def state_dir(root: Path) -> Path:
    return Path(root) / STATE_DIR


def iter_source_files(root: Path) -> list[Path]:
    """Repo-relative paths of every tracked source file, sorted for determinism."""
    root = Path(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ALWAYS_IGNORED)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix in SOURCE_SUFFIXES:
                found.append(path.relative_to(root))
    return sorted(found, key=lambda p: p.as_posix())


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def hash_file(path: Path) -> str:
    return hash_text(read_text(path))


def count_lines(text: str) -> int:
    return len(text.splitlines())


def normalize(path: str | Path) -> str:
    """Repo-relative posix form, with any leading ./ stripped."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def matches_any(path: str | Path, patterns: list[str]) -> str | None:
    """Return the first glob in `patterns` matching `path`, else None.

    `tests/**` is treated as covering `tests/` itself and everything under it,
    which fnmatch alone does not do.
    """
    target = normalize(path)
    for pattern in patterns:
        pattern = normalize(pattern)
        if fnmatch.fnmatch(target, pattern):
            return pattern
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if target == prefix or target.startswith(prefix + "/"):
                return pattern
    return None


def git_commit(root: Path) -> str | None:
    """Current HEAD, or None when the project is not a git repo. Never fatal."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env specific
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def find_root(start: Path | None = None) -> Path:
    """Nearest governed ancestor, including one whose in-tree marker was deleted."""
    current = Path(start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / STATE_DIR).is_dir():
            return candidate
        try:
            from .authority import Authority

            if Authority.for_root(candidate).exists:
                return candidate
        except (ImportError, OSError):  # pragma: no cover - startup/platform edge
            pass
    return current
