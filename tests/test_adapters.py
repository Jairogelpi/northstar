from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from northstar import adapters, evidence
from northstar.adapters import EXIT_BLOCK, EXIT_OK, HookEvent, handle, parse_event

from .conftest import SERVICE, write


def run(payload: dict, root: Path) -> tuple[int, str]:
    err = io.StringIO()
    code = handle(payload, root, stderr=err)
    return code, err.getvalue()


# ------------------------------------------------------------ normalisation


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}},
        {"event": "pre_tool", "tool": "Edit", "arguments": {"file_path": "a.py"}},
        {"phase": "pre", "name": "Edit", "input": {"file_path": "a.py"}},
        {"hook": "pre", "toolName": "Edit", "toolInput": {"file_path": "a.py"}},
    ],
)
def test_every_agent_dialect_normalises_to_the_same_event(payload):
    event = parse_event(payload)
    assert event.tool == "Edit"
    assert event.params == {"file_path": "a.py"}
    assert not event.is_post


def test_post_events_are_recognised():
    assert parse_event({"hook_event_name": "PostToolUse"}).is_post
    assert HookEvent("Edit", {}, "post_tool", None).is_post


def test_missing_fields_degrade_to_empty():
    event = parse_event({})
    assert event.tool == "" and event.params == {} and event.cwd is None


def test_cwd_aliases():
    assert parse_event({"cwd": "/a"}).cwd == "/a"
    assert parse_event({"workspace": "/b"}).cwd == "/b"
    assert parse_event({"project_dir": "/c"}).cwd == "/c"
    assert parse_event({"cwd": 5}).cwd is None


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[1,2]"])
def test_unreadable_payload_never_wedges_the_agent(raw):
    assert adapters.read_payload(io.StringIO(raw)) == {}


def test_read_payload_parses_json():
    assert adapters.read_payload(io.StringIO('{"tool":"Edit"}')) == {"tool": "Edit"}


# -------------------------------------------------------------- pre-tool gate


def test_pre_hook_blocks_a_protected_write(governed):
    project, _, _ = governed
    code, message = run(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "tests/test_auth.py"}},
        project,
    )
    assert code == EXIT_BLOCK
    assert "NORTHSTAR" in message
    assert "protected" in message
    assert 'northstar request --grant "protected_path:tests/test_auth.py"' in message
    assert "do not edit .northstar/" in message


def test_block_message_restates_the_objective(governed):
    project, contract, _ = governed
    _, message = run(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "tests/x.py"}},
        project,
    )
    assert contract.objective in message
    assert "contract v1" in message


def test_a_non_amendable_refusal_offers_no_grant(governed):
    """A refused command must not be answered with 'ask them to allow this command'."""
    project, _, _ = governed
    code, message = run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "northstar approve deadbeef"},
        },
        project,
    )
    assert code == EXIT_BLOCK
    assert "not amendable" in message
    assert "northstar amend --grant" not in message.split("[DENY]")[1]
    assert "grant needed" not in message


def test_forbidden_command_is_not_amendable_either(governed):
    project, _, _ = governed
    _, message = run(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push"}},
        project,
    )
    assert "not amendable" in message


def test_pre_hook_allows_an_ordinary_write(governed):
    project, _, _ = governed
    code, message = run(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "src/db.py"}},
        project,
    )
    assert code == EXIT_OK and message == ""


def test_nested_cwd_cannot_change_project_identity_or_escape_with_traversal(governed):
    project, _, _ = governed
    nested = project / "src"
    code, message = run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "../.northstar/oracle.json"},
            "cwd": str(nested),
        },
        nested,
    )
    assert code == EXIT_BLOCK
    assert "protected" in message


def test_allowed_gate_calls_are_not_journalled(governed):
    project, _, _ = governed
    run({"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}, project)
    assert evidence.read_journal(project) == []


def test_blocked_gate_calls_are_journalled(governed):
    project, _, _ = governed
    run(
        {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": "tests/a.py"}},
        project,
    )
    entry = evidence.read_journal(project)[0]
    assert entry.phase == "gate" and entry.decision == "DENY"


# ------------------------------------------------------------- post-tool check


def test_post_hook_detects_trajectory_drift(governed):
    project, _, _ = governed
    write(project, "src/auth/service.py", SERVICE.replace("password: str", "pwd: str"))
    code, message = run({"hook_event_name": "PostToolUse", "tool_name": "Edit"}, project)
    assert code == EXIT_BLOCK
    assert "public_api" in message
    assert evidence.read_journal(project)[0].phase == "check"


def test_post_hook_is_quiet_on_a_clean_tree(governed):
    project, _, _ = governed
    code, message = run({"hook_event_name": "PostToolUse", "tool_name": "Edit"}, project)
    assert code == EXIT_OK and message == ""


def test_post_hook_surfaces_unknown_without_blocking(governed):
    project, _, _ = governed
    write(project, "src/broken.py", "def (:\n")
    code, message = run({"hook_event_name": "PostToolUse", "tool_name": "Write"}, project)
    assert code == EXIT_OK
    assert "UNKNOWN" in message


# ----------------------------------------------------------------- guardrails


def test_ungoverned_project_is_left_alone(tmp_path: Path):
    code, message = run(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}},
        tmp_path,
    )
    assert code == EXIT_OK and message == ""


def test_legacy_or_missing_oracle_fails_closed(project: Path):
    from northstar.contract import default_contract

    default_contract("x").save(project)
    code, _ = run({"hook_event_name": "PostToolUse", "tool_name": "Edit"}, project)
    assert code == EXIT_BLOCK


def test_hook_rereads_the_contract_every_call(governed):
    """The contract is never remembered, only re-read -- immune to compaction."""
    project, contract, _ = governed
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "src/db.py"}}
    assert run(payload, project)[0] == EXIT_OK

    from northstar.authority import Authority

    authority = Authority.open(project, required=True)
    assert authority is not None
    live, oracle = authority.load()
    live.constraints["protected_paths"].append("src/db.py")
    authority.persist(live, oracle)
    assert run(payload, project)[0] == EXIT_BLOCK


def test_payload_survives_json_roundtrip(governed):
    project, _, _ = governed
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "tests/a.py"}})
    assert handle(adapters.read_payload(io.StringIO(raw)), project, stderr=io.StringIO()) == EXIT_BLOCK
