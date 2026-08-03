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
from pathlib import Path

from .util import read_text

HOOK_COMMAND = "northstar hook"

AGENTS_BEGIN = "<!-- northstar:begin -->"
AGENTS_END = "<!-- northstar:end -->"

AGENTS_BLOCK = f"""{AGENTS_BEGIN}
## Northstar intent contract (enforced)

This project is governed by an executable intent contract at
`.northstar/contract.yaml`, frozen against a baseline in `.northstar/oracle.json`.

Rules, in order of precedence:

1. Never write to `.northstar/`. It is the grader; editing it invalidates the run.
2. Never edit a path listed in `constraints.protected_paths` -- tests included.
   Making a test pass by editing the test is the failure mode this exists to stop.
3. Run `northstar check` after any group of edits. A non-zero exit means the
   working tree has diverged from the contract. Fix the divergence; do not
   suppress the check.
4. You may not amend the contract. If a constraint genuinely blocks the
   objective, stop and tell the human the exact grant you need, in the form
   `northstar amend --grant "<kind>:<identifier>" --reason "..."`.
   Only a human signs. Requesting is allowed; self-signing is not.
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


def _hook_entry() -> dict:
    return {
        "matcher": "*",
        "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 30}],
    }


def _already_installed(groups: list) -> bool:
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
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
        if not _already_installed(groups):
            groups.append(_hook_entry())
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
    body = 'notify = ["northstar", "hook"]\n'
    if not config.exists() or "northstar" not in read_text(config):
        config.parent.mkdir(parents=True, exist_ok=True)
        previous = read_text(config) if config.exists() else ""
        separator = "\n" if previous and not previous.endswith("\n") else ""
        config.write_text(previous + separator + body, encoding="utf-8")
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
