from __future__ import annotations

import json
from pathlib import Path

from northstar import install as inst
from northstar.util import read_text


def settings(root: Path) -> dict:
    return json.loads(read_text(root / ".claude" / "settings.json"))


def test_claude_hooks_are_registered(tmp_path: Path):
    inst.install_claude(tmp_path)
    hooks = settings(tmp_path)["hooks"]
    for event in ("PreToolUse", "PostToolUse"):
        assert hooks[event][0]["hooks"][0]["command"] == inst.hook_command(tmp_path)
        assert hooks[event][0]["matcher"] == "*"


def test_install_is_idempotent(tmp_path: Path):
    inst.install_claude(tmp_path)
    inst.install_claude(tmp_path)
    assert len(settings(tmp_path)["hooks"]["PreToolUse"]) == 1


def test_existing_settings_are_preserved(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}),
        encoding="utf-8",
    )
    inst.install_claude(tmp_path)
    data = settings(tmp_path)
    assert data["model"] == "opus"
    assert len(data["hooks"]["PreToolUse"]) == 2  # ours appended, theirs kept


def test_malformed_settings_do_not_crash_the_install(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    inst.install_claude(tmp_path)
    assert settings(tmp_path)["hooks"]["PreToolUse"]


def test_wrong_shaped_hook_keys_are_repaired(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": "nonsense"}), encoding="utf-8")
    inst.install_claude(tmp_path)
    assert settings(tmp_path)["hooks"]["PreToolUse"]

    path.write_text(json.dumps({"hooks": {"PreToolUse": "nonsense"}}), encoding="utf-8")
    inst.install_claude(tmp_path)
    assert isinstance(settings(tmp_path)["hooks"]["PreToolUse"], list)


def test_agents_block_written_once_and_refreshed(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# House rules\n\nBe nice.\n", encoding="utf-8")
    inst.install_agents_md(tmp_path)
    inst.install_agents_md(tmp_path)

    text = read_text(tmp_path / "AGENTS.md")
    assert text.count(inst.AGENTS_BEGIN) == 1
    assert "Be nice." in text
    assert "Never write to `.northstar/`" in text
    assert "may request, but may not approve" in text


def test_agents_block_created_when_file_absent(tmp_path: Path):
    inst.install_agents_md(tmp_path, "CLAUDE.md")
    assert inst.AGENTS_BEGIN in read_text(tmp_path / "CLAUDE.md")


def test_stale_block_is_replaced_not_duplicated(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        f"before\n{inst.AGENTS_BEGIN}\nold text\n{inst.AGENTS_END}\nafter\n", encoding="utf-8"
    )
    inst.install_agents_md(tmp_path)
    text = read_text(tmp_path / "AGENTS.md")
    assert "old text" not in text
    assert text.startswith("before") and text.rstrip().endswith("after")


def test_codex_gets_instructions_and_native_hooks(tmp_path: Path):
    written = inst.install_codex(tmp_path)
    assert (tmp_path / "AGENTS.md").exists()
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks = json.loads(read_text(hooks_path))["hooks"]
    for event in ("PreToolUse", "PostToolUse"):
        assert hooks[event][0]["hooks"][0]["command"] == inst.hook_command(tmp_path)
    assert len(written) == 2

    inst.install_codex(tmp_path)  # idempotent
    hooks = json.loads(read_text(hooks_path))["hooks"]
    assert len(hooks["PreToolUse"]) == 1
    assert len(hooks["PostToolUse"]) == 1


def test_codex_hooks_preserve_existing_hooks(tmp_path: Path):
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}),
        encoding="utf-8",
    )
    inst.install_codex(tmp_path)
    hooks = json.loads(read_text(hooks_path))["hooks"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == "echo hi"
    assert hooks["PreToolUse"] and hooks["PostToolUse"]


def test_codex_removes_only_its_ignored_legacy_notify(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        'model = "gpt-5"\nnotify = ["northstar", "--root", "C:\\\\repo", "hook"]\n',
        encoding="utf-8",
    )
    inst.install_codex(tmp_path)
    text = read_text(config)
    assert 'model = "gpt-5"' in text
    assert "notify" not in text


def test_codex_repairs_wrong_shaped_hook_document(tmp_path: Path):
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"hooks": "broken"}), encoding="utf-8")
    inst.install_codex(tmp_path)
    hooks = json.loads(read_text(hooks_path))["hooks"]
    assert isinstance(hooks["PreToolUse"], list)
    assert isinstance(hooks["PostToolUse"], list)


def test_integrity_rejects_ignored_legacy_codex_wiring(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('notify = ["northstar", "hook"]\n', encoding="utf-8")

    issues = inst.integrity_issues(tmp_path, [".codex/config.toml"])

    assert len(issues) == 1
    assert "Codex ignores" in issues[0]
    assert "northstar install --agent codex" in issues[0]


def test_install_wires_both_agents_by_default(tmp_path: Path):
    written = {Path(p).name for p in inst.install(tmp_path)}
    assert written == {"settings.json", "CLAUDE.md", "AGENTS.md", "hooks.json"}


def test_install_can_target_one_agent(tmp_path: Path):
    inst.install(tmp_path, ["claude"])
    assert not (tmp_path / ".codex").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()


def test_uninstall_preserves_foreign_claude_settings_and_hooks(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo safe"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Team rules\n", encoding="utf-8")
    inst.install_claude(tmp_path)
    inst.install_agents_md(tmp_path, "CLAUDE.md")

    touched = inst.uninstall_claude(tmp_path)

    data = json.loads(read_text(path))
    assert data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo safe"
    assert all(inst.hook_command(tmp_path) not in read_text(item) for item in touched if item.exists())
    assert read_text(tmp_path / "CLAUDE.md") == "# Team rules\n"


def test_uninstall_codex_removes_only_managed_content(tmp_path: Path):
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo hello"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# Existing\n", encoding="utf-8")
    inst.install_codex(tmp_path)

    inst.uninstall_codex(tmp_path)

    document = json.loads(read_text(hooks_path))
    assert document["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo hello"
    assert "PreToolUse" not in document["hooks"]
    assert read_text(tmp_path / "AGENTS.md") == "# Existing\n"


def test_uninstall_deletes_files_that_contain_only_managed_content(tmp_path: Path):
    inst.install(tmp_path)
    inst.uninstall(tmp_path)

    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()
