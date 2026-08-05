"""Zero-configuration wiring for Claude Code and Codex.

Idempotent by construction: installing twice changes nothing, and existing user
settings are merged, never overwritten.

Honest about the difference between the two: Claude Code exposes a pre-tool hook
that can *block*, so northstar stops the write before it happens. Codex has no
equivalent blocking hook, so there it runs as post-hoc detection plus an
instruction block the agent reads. Detection without prevention is still worth
having -- but it is not the same guarantee, and pretending otherwise would be
the exact dishonesty this project exists to avoid.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .util import read_text

AGENTS_BEGIN = "<!-- northstar:begin -->"
AGENTS_END = "<!-- northstar:end -->"

AGENTS_BLOCK = f"""{AGENTS_BEGIN}
## Northstar intent contract (enforced)

This project is governed by an executable intent contract. `.northstar/` contains
readable mirrors; the runtime verifies a sealed authority outside the working tree.

Rules, in order of precedence:

1. Never write to `.northstar/` or Northstar's hook wiring. Tampering is a blocking
   integrity failure; the working-tree files are not the runtime authority.
2. Never edit a path listed in `constraints.protected_paths` -- tests included.
   Making a test pass by editing the test is the failure mode this exists to stop.
3. Run `northstar check` after any group of edits. A non-zero exit means the
   working tree has diverged from the contract. Fix the divergence; do not
   suppress the check.
4. You may request, but may not approve, a contract amendment. If a constraint genuinely blocks the
   objective, stop and tell the human the exact grant you need, in the form
   `northstar request --grant "<kind>:<identifier>" --reason "..."`.
   A human approves the request from a separate interactive terminal.
5. `northstar status` restates the objective and the current verdict. Read it
   after any context compaction or handoff, rather than trusting your memory of
   the original request.
{AGENTS_END}"""


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def hook_command(root: Path) -> str:
    """Shell command bound to one checkout, quoted for the current platform."""
    argv = ["northstar", "--root", str(Path(root).resolve()), "hook"]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def codex_notify(root: Path) -> str:
    """TOML assignment for a root-bound Codex notification command."""
    return "notify = " + json.dumps(
        ["northstar", "--root", str(Path(root).resolve()), "hook"]
    )


def _replace_notify(previous: str, body: str) -> str:
    if re.search(r"(?m)^\s*notify\s*=.*$", previous):
        # A replacement string would interpret Windows backslashes a second
        # time. The callback returns the JSON/TOML text literally.
        return re.sub(r"(?m)^\s*notify\s*=.*$", lambda _: body, previous)
    separator = "\n" if previous and not previous.endswith("\n") else ""
    return previous + separator + body + "\n"


def _hook_entry(root: Path) -> dict:
    return {
        "matcher": "*",
        "hooks": [{"type": "command", "command": hook_command(root), "timeout": 30}],
    }


def _already_installed(groups: list, root: Path) -> bool:
    expected = hook_command(root)
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == expected:
                return True
    return False


def install_claude(root: Path) -> Path:
    """Register blocking PreToolUse + observing PostToolUse hooks."""
    settings_path = Path(root) / ".claude" / "settings.json"
    settings = _load_json(settings_path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):  # a malformed key must not silently vanish
        hooks = {}
        settings["hooks"] = hooks
    for event in ("PreToolUse", "PostToolUse"):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        if not _already_installed(groups, root):
            groups.append(_hook_entry(root))
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings_path


def install_agents_md(root: Path, filename: str = "AGENTS.md") -> Path:
    """Write (or refresh) the northstar block in an agent instruction file."""
    path = Path(root) / filename
    existing = read_text(path) if path.exists() else ""
    if AGENTS_BEGIN in existing and AGENTS_END in existing:
        head, _, rest = existing.partition(AGENTS_BEGIN)
        _, _, tail = rest.partition(AGENTS_END)
        updated = f"{head}{AGENTS_BLOCK}{tail}"
    else:
        separator = "\n\n" if existing.strip() else ""
        updated = f"{existing.rstrip()}{separator}{AGENTS_BLOCK}\n"
    path.write_text(updated, encoding="utf-8")
    return path


def install_codex(root: Path) -> list[Path]:
    """Codex reads AGENTS.md; the notify hook gives post-hoc journalling."""
    written = [install_agents_md(root, "AGENTS.md")]
    config = Path(root) / ".codex" / "config.toml"
    body = codex_notify(root)
    config.parent.mkdir(parents=True, exist_ok=True)
    previous = read_text(config) if config.exists() else ""
    updated = _replace_notify(previous, body)
    if updated != previous:
        config.write_text(updated, encoding="utf-8")
    written.append(config)
    return written


def install(root: Path, agents: list[str] | None = None) -> list[Path]:
    targets = agents or ["claude", "codex"]
    written: list[Path] = []
    if "claude" in targets:
        written.append(install_claude(root))
        written.append(install_agents_md(root, "CLAUDE.md"))
    if "codex" in targets:
        written.extend(install_codex(root))
    return written


def integrity_issues(root: Path, expected: list[str]) -> list[str]:
    """Structural verification of the exact wiring created during ``init``.

    User-owned settings may legitimately change, so hashing whole files would be
    brittle.  The authority instead seals the list of integrations and verifies
    that Northstar's hook/block is still present in each one.
    """
    root = Path(root)
    issues: list[str] = []
    for relative in expected:
        path = root / relative
        if not path.exists():
            issues.append(f"{relative} is missing")
            continue
        normal = relative.replace("\\", "/")
        if normal == ".claude/settings.json":
            data = _load_json(path)
            hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
            for event in ("PreToolUse", "PostToolUse"):
                groups = hooks.get(event, []) if isinstance(hooks, dict) else []
                if not isinstance(groups, list) or not _already_installed(groups, root):
                    issues.append(f"{relative} has no {event} Northstar hook")
        elif normal in ("AGENTS.md", "CLAUDE.md"):
            text = read_text(path)
            if AGENTS_BEGIN not in text or AGENTS_END not in text:
                issues.append(f"{relative} has no Northstar instruction block")
        elif normal == ".codex/config.toml":
            text = read_text(path)
            if codex_notify(root) not in text:
                issues.append(f"{relative} has no Northstar notify hook")
    return issues


def discover_wiring(root: Path) -> list[Path]:
    """Existing valid v0.1 integrations that can be sealed during migration."""
    root = Path(root)
    candidates = [
        ".claude/settings.json",
        "CLAUDE.md",
        "AGENTS.md",
        ".codex/config.toml",
    ]
    return [root / relative for relative in candidates if not integrity_issues(root, [relative])]
