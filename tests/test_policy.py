from __future__ import annotations

import pytest

from northstar import checks, policy
from northstar.contract import Contract, default_contract
from northstar.policy import Decision

from .conftest import SERVICE, write


def finding(kind: str, identifier: str = "x") -> checks.Finding:
    return checks.Finding(kind, identifier, "detail")


# --------------------------------------------------------------- judge


def test_protected_path_always_denies():
    c = default_contract("x")
    assert policy.judge(c, finding(checks.PROTECTED_PATH)).decision is Decision.DENY


def test_rule_drives_the_verdict():
    forbidden = Contract(objective="x", constraints={"dependencies": {"additions": "forbidden"}})
    approval = Contract(objective="x", constraints={"dependencies": {"additions": "approval_required"}})
    allowed = Contract(objective="x", constraints={"dependencies": {"additions": "allowed"}})

    assert policy.judge(forbidden, finding(checks.DEPENDENCY)).decision is Decision.DENY
    assert policy.judge(approval, finding(checks.DEPENDENCY)).decision is Decision.REQUIRE_APPROVAL
    assert policy.judge(allowed, finding(checks.DEPENDENCY)).decision is Decision.ALLOW


def test_scope_asks_rather_than_blocks():
    assert policy.judge(default_contract("x"), finding(checks.SCOPE)).decision is Decision.REQUIRE_APPROVAL


def test_unknown_is_first_class_and_never_pretends_to_be_clean():
    j = policy.judge(default_contract("x"), finding(checks.UNKNOWN_KIND))
    assert j.decision is Decision.UNKNOWN


def test_api_addition_uses_its_own_rule():
    strict = Contract(objective="x", constraints={"public_api": {"additions": "forbidden"}})
    assert policy.judge(strict, finding(checks.API_ADDITION)).decision is Decision.DENY
    assert policy.judge(default_contract("x"), finding(checks.API_ADDITION)).decision is Decision.ALLOW


def test_module_edge_rule():
    strict = Contract(objective="x", constraints={"module_graph": {"new_edges": "forbidden"}})
    assert policy.judge(strict, finding(checks.MODULE_EDGE)).decision is Decision.DENY


def test_signed_amendment_turns_a_denial_into_an_allow():
    c = default_contract("x")
    c.amend("agreed with the human", ["dependency:httpx"])
    j = policy.judge(c, finding(checks.DEPENDENCY, "httpx"))
    assert j.decision is Decision.ALLOW
    assert j.amendment_version == 2
    assert "amendment v2" in j.reason


def test_amendment_does_not_leak_to_a_sibling():
    c = default_contract("x")
    c.amend("only httpx", ["dependency:httpx"])
    assert policy.judge(c, finding(checks.DEPENDENCY, "boto3")).decision is Decision.DENY


def test_worst_decision_wins():
    assert policy._worst([Decision.ALLOW, Decision.UNKNOWN, Decision.DENY]) is Decision.DENY
    assert policy._worst([]) is Decision.ALLOW


# ------------------------------------------------------------- evaluate


def test_evaluate_clean_tree(governed):
    project, contract, oracle = governed
    verdict = policy.evaluate(contract, oracle, project)
    assert verdict.decision is Decision.ALLOW
    assert not verdict.is_blocking
    assert "on course" in verdict.summary()


def test_evaluate_reports_every_divergence_at_once(governed):
    project, contract, oracle = governed
    write(project, "tests/test_auth.py", "# gutted\n")
    write(project, "pyproject.toml", '[project]\ndependencies = ["requests", "pyyaml", "httpx"]\n')
    write(project, "src/auth/service.py", SERVICE.replace("password: str", "pwd: str"))

    verdict = policy.evaluate(contract, oracle, project)
    kinds = {j.finding.kind for j in verdict.judgements if j.finding}
    assert {checks.PROTECTED_PATH, checks.DEPENDENCY, checks.PUBLIC_API} <= kinds
    assert verdict.decision is Decision.DENY
    assert len(verdict.blocking) >= 3
    assert "[DENY]" in verdict.summary()


def test_evaluate_serialises(governed):
    project, contract, oracle = governed
    data = policy.evaluate(contract, oracle, project).to_dict()
    assert data["decision"] == "ALLOW"
    assert data["judgements"] == []


# ----------------------------------------------------------------- gate


def test_gate_blocks_a_write_to_a_protected_path(governed):
    project, contract, _ = governed
    verdict = policy.gate(contract, "Edit", {"file_path": "tests/test_auth.py"}, project)
    assert verdict.decision is Decision.DENY
    assert "protected" in verdict.blocking[0].reason


def test_gate_blocks_writes_to_the_grader_itself(governed):
    project, contract, _ = governed
    verdict = policy.gate(contract, "Write", {"file_path": ".northstar/oracle.json"}, project)
    assert verdict.decision is Decision.DENY


def test_gate_resolves_absolute_paths(governed):
    project, contract, _ = governed
    absolute = str(project / "tests" / "test_auth.py")
    assert policy.gate(contract, "Edit", {"file_path": absolute}, project).decision is Decision.DENY


def test_gate_keeps_foreign_absolute_paths_as_is(governed):
    project, contract, _ = governed
    verdict = policy.gate(contract, "Edit", {"file_path": "/elsewhere/tests/x.py"}, project)
    assert verdict.decision is Decision.ALLOW


def test_gate_allows_an_ordinary_edit(governed):
    project, contract, _ = governed
    assert policy.gate(contract, "Edit", {"file_path": "src/auth/service.py"}, project).decision is Decision.ALLOW


def test_gate_reads_multiedit_shapes(governed):
    project, contract, _ = governed
    params = {"edits": [{"file_path": "src/db.py"}, {"file_path": "tests/test_auth.py"}]}
    assert policy.gate(contract, "MultiEdit", params, project).decision is Decision.DENY


def test_gate_extracts_paths_from_apply_patch(governed):
    project, contract, _ = governed
    params = {"patch": "*** Begin Patch\n*** Update File: .northstar/oracle.json\n@@\n-{}\n+[]\n*** End Patch"}
    assert policy.gate(contract, "apply_patch", params, project).decision is Decision.DENY


def test_gate_extracts_codex_apply_patch_command(governed):
    project, contract, _ = governed
    params = {
        "command": (
            "*** Begin Patch\n*** Update File: .northstar/oracle.json\n"
            "@@\n-{}\n+[]\n*** End Patch"
        )
    }
    assert policy.gate(contract, "apply_patch", params, project).decision is Decision.DENY


def test_path_traversal_and_symlinks_resolve_before_matching(governed):
    project, contract, _ = governed
    assert policy.gate(
        contract, "Write", {"file_path": "nested/../.northstar/oracle.json"}, project
    ).decision is Decision.DENY
    link = project / "grader-link"
    try:
        link.symlink_to(project / ".northstar", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    assert policy.gate(
        contract, "Write", {"file_path": "grader-link/oracle.json"}, project
    ).decision is Decision.DENY


def test_relative_path_is_resolved_from_action_cwd(governed):
    project, contract, _ = governed
    assert policy.gate(
        contract,
        "Write",
        {"file_path": "../.northstar/oracle.json"},
        project,
        cwd=project / "src",
    ).decision is Decision.DENY


def test_gate_ignores_read_only_tools(governed):
    project, contract, _ = governed
    assert policy.gate(contract, "Read", {"file_path": "tests/test_auth.py"}, project).decision is Decision.ALLOW


@pytest.mark.parametrize("command", ["git push origin main", "rm -rf /tmp/x", "cd src && git push"])
def test_gate_blocks_forbidden_commands(governed, command):
    project, contract, _ = governed
    assert policy.gate(contract, "Bash", {"command": command}, project).decision is Decision.DENY


def test_gate_allows_ordinary_commands(governed):
    project, contract, _ = governed
    assert policy.gate(contract, "Bash", {"command": "pytest -q"}, project).decision is Decision.ALLOW


@pytest.mark.parametrize(
    "command",
    [
        "rm .northstar/contract.yaml",
        "python -c \"from pathlib import Path; Path('.northstar/oracle.json').unlink()\"",
        "sed -i 's/x/y/' .northstar/contract.yaml",
        "mv replacement .northstar/oracle.json",
        "printf '{}' > .northstar/oracle.json",
        "cd .northstar && rm oracle.json",
        "rm .claude/settings.json",
        "python -c \"from northstar.authority import Authority; Authority.for_root('.').seal()\"",
    ],
)
def test_shell_cannot_mutate_authority_mirrors_or_wiring(governed, command):
    project, contract, _ = governed
    verdict = policy.gate(contract, "Bash", {"command": command}, project)
    assert verdict.decision is Decision.DENY
    assert verdict.blocking[0].finding.kind == checks.GOVERNANCE


def test_unknown_tools_are_blocking_until_capability_is_declared(governed):
    project, contract, _ = governed
    params = {"path": "src/db.py"}
    assert policy.gate(contract, "mcp_custom_writer", params, project).decision is Decision.REQUIRE_APPROVAL

    contract.constraints["tools"]["read_only"] = ["mcp_custom_reader*"]
    assert policy.gate(contract, "mcp_custom_reader_v2", params, project).decision is Decision.ALLOW

    contract.constraints["tools"]["mutating"] = ["mcp_custom_writer"]
    assert policy.gate(contract, "mcp_custom_writer", params, project).decision is Decision.ALLOW
    assert policy.gate(
        contract, "mcp_custom_writer", {"path": ".northstar/oracle.json"}, project
    ).decision is Decision.DENY


def test_gate_ignores_empty_command(governed):
    project, contract, _ = governed
    assert policy.gate(contract, "Bash", {"command": "   "}, project).judgements == []


def test_gate_accepts_alternate_command_key(governed):
    project, contract, _ = governed
    assert policy.gate(contract, "shell", {"cmd": "git push"}, project).decision is Decision.DENY


@pytest.mark.parametrize(
    "command",
    [
        "northstar approve abc",
        "python -m northstar freeze --reason x",
        "northstar migrate --accept-existing-state",
    ],
)
def test_agent_cannot_approve_or_rebaseline(governed, command):
    project, contract, _ = governed
    verdict = policy.gate(contract, "Bash", {"command": command}, project)
    assert verdict.decision is Decision.DENY
    assert "human-only" in verdict.blocking[0].reason


def test_agent_may_create_an_untrusted_request(governed):
    project, contract, _ = governed
    command = "northstar request --grant public_api:x --reason y"
    assert policy.gate(contract, "Bash", {"command": command}, project).decision is Decision.ALLOW


def test_agent_may_still_run_read_only_northstar_commands(governed):
    project, contract, _ = governed
    for command in ("northstar check", "northstar status"):
        assert policy.gate(contract, "Bash", {"command": command}, project).decision is Decision.ALLOW


@pytest.mark.parametrize("kind", [checks.COMMAND, checks.GOVERNANCE])
def test_gate_only_refusals_cannot_be_signed_away(kind):
    """No signature retroactively authorises a refused action."""
    assert policy.judge(default_contract("x"), finding(kind, "anything")).decision is Decision.DENY

    signed = default_contract("x")
    signed.amend("tried to grant a refusal", [f"{kind}:*"])
    judgement = policy.judge(signed, finding(kind, "anything"))
    assert judgement.decision is Decision.DENY
    assert judgement.amendment_version is None


def test_split_command():
    assert policy._split_command("a && b; c | d") == ["a", "b", "c", "d"]
