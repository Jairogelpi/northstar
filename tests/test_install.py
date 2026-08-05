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


def test_codex_gets_instructions_and_a_notify_hook(tmp_path: Path):
    written = inst.install_codex(tmp_path)
    assert (tmp_path / "AGENTS.md").exists()
    config = read_text(tmp_path / ".codex" / "config.toml")
    assert inst.codex_notify(tmp_path) in config
    assert len(written) == 2

    inst.install_codex(tmp_path)  # idempotent
    assert read_text(tmp_path / ".codex" / "config.toml").count("notify") == 1


def test_codex_config_appends_to_existing(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "gpt-5"', encoding="utf-8")
    inst.install_codex(tmp_path)
    text = read_text(config)
    assert 'model = "gpt-5"' in text and "notify" in text


def test_codex_replaces_an_unbound_notify_hook(tmp_path: Path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('notify = ["northstar", "hook"]\n', encoding="utf-8")
    inst.install_codex(tmp_path)
    text = read_text(config)
    assert inst.codex_notify(tmp_path) in text
    assert text.count("notify") == 1


def test_install_wires_both_agents_by_default(tmp_path: Path):
    written = {Path(p).name for p in inst.install(tmp_path)}
    assert written == {"settings.json", "CLAUDE.md", "AGENTS.md", "config.toml"}


def test_install_can_target_one_agent(tmp_path: Path):
    inst.install(tmp_path, ["claude"])
    assert not (tmp_path / ".codex").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
