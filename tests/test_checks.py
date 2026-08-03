from __future__ import annotations

from pathlib import Path

from northstar import checks
from northstar.contract import Contract, default_contract
from northstar.freeze import Oracle, freeze

from .conftest import SERVICE, write


def state_of(project: Path, contract: Contract) -> checks.TreeState:
    return checks.read_tree(project, contract.api_scope)


def run(project: Path, contract: Contract, oracle: Oracle) -> list[checks.Finding]:
    return checks.run_all(contract, oracle, state_of(project, contract))


def test_clean_tree_has_no_findings(governed):
    project, contract, oracle = governed
    assert run(project, contract, oracle) == []


def test_finding_grant_string():
    f = checks.Finding("public_api", "m.py::f", "changed")
    assert f.grant == "public_api:m.py::f"
    assert f.to_dict()["kind"] == "public_api"


# ------------------------------------------------------------ protected paths


def test_modifying_a_protected_test_is_caught(governed):
    project, contract, oracle = governed
    write(project, "tests/test_auth.py", "def test_login():\n    assert True\n")

    found = checks.check_protected_paths(contract, oracle, state_of(project, contract))
    assert [f.identifier for f in found] == ["tests/test_auth.py"]
    assert "modified" in found[0].detail


def test_deleting_a_protected_file_is_caught(governed):
    project, contract, oracle = governed
    (project / "tests" / "test_auth.py").unlink()
    found = checks.check_protected_paths(contract, oracle, state_of(project, contract))
    assert "deleted" in found[0].detail


def test_creating_a_file_under_a_protected_path_is_caught(governed):
    project, contract, oracle = governed
    write(project, "tests/test_new.py", "def test_x():\n    pass\n")
    found = checks.check_protected_paths(contract, oracle, state_of(project, contract))
    assert found[0].identifier == "tests/test_new.py"
    assert "created" in found[0].detail


def test_unprotected_edits_are_ignored(governed):
    project, contract, oracle = governed
    write(project, "src/db.py", "def connect():\n    return False\n")
    assert checks.check_protected_paths(contract, oracle, state_of(project, contract)) == []


# ---------------------------------------------------------------- public api


def test_signature_change_is_caught(governed):
    project, contract, oracle = governed
    write(project, "src/auth/service.py", SERVICE.replace("def login(user: str, password: str) -> bool:", "def login(user: str, password: str, mfa: str) -> bool:"))
    found = checks.check_public_api(contract, oracle, state_of(project, contract))
    assert found[0].identifier == "src/auth/service.py::login"
    assert "signature changed" in found[0].detail


def test_symbol_removal_is_caught(governed):
    project, contract, oracle = governed
    write(project, "src/auth/service.py", "from db import connect\n")
    removed = {f.identifier for f in checks.check_public_api(contract, oracle, state_of(project, contract))}
    assert "src/auth/service.py::login" in removed
    assert "src/auth/service.py::Session.refresh" in removed


def test_added_symbol_is_a_separate_kind(governed):
    project, contract, oracle = governed
    write(project, "src/auth/service.py", SERVICE + "\n\ndef logout() -> None:\n    pass\n")
    found = checks.check_public_api(contract, oracle, state_of(project, contract))
    assert [(f.kind, f.identifier) for f in found] == [
        (checks.API_ADDITION, "src/auth/service.py::logout")
    ]


def test_private_helpers_may_change_freely(governed):
    project, contract, oracle = governed
    write(project, "src/auth/service.py", SERVICE.replace("def _hash(value):", "def _hash(value, salt=None):"))
    assert checks.check_public_api(contract, oracle, state_of(project, contract)) == []


def test_api_violation_survives_a_later_fix_attempt(governed):
    """State-not-delta: breaking then 'restoring' differently is still a violation."""
    project, contract, oracle = governed
    write(project, "src/auth/service.py", SERVICE.replace("password: str", "pwd: str"))
    assert checks.check_public_api(contract, oracle, state_of(project, contract))


# -------------------------------------------------------------- dependencies


def test_added_dependency_is_caught(governed):
    project, contract, oracle = governed
    write(project, "pyproject.toml", '[project]\ndependencies = ["requests", "pyyaml", "httpx"]\n')
    found = checks.check_dependencies(contract, oracle, state_of(project, contract))
    assert [f.identifier for f in found] == ["httpx"]


def test_removed_dependency_is_not_an_addition(governed):
    project, contract, oracle = governed
    write(project, "pyproject.toml", '[project]\ndependencies = ["requests"]\n')
    assert checks.check_dependencies(contract, oracle, state_of(project, contract)) == []


def test_new_manifest_counts_all_its_deps(governed):
    project, contract, oracle = governed
    write(project, "requirements.txt", "boto3\n")
    found = checks.check_dependencies(contract, oracle, state_of(project, contract))
    assert [f.identifier for f in found] == ["boto3"]


# -------------------------------------------------------------- module graph


def test_new_module_edge_is_caught(governed):
    project, contract, oracle = governed
    write(project, "src/db.py", "from auth.service import login\n\n\ndef connect():\n    return True\n")
    found = checks.check_module_graph(contract, oracle, state_of(project, contract))
    assert found[0].identifier == "db->auth.service"


def test_external_imports_are_not_edges(governed):
    project, contract, oracle = governed
    write(project, "src/db.py", "import json\n\n\ndef connect():\n    return True\n")
    assert checks.check_module_graph(contract, oracle, state_of(project, contract)) == []


# ---------------------------------------------------------------------- scope


def test_scope_budget_on_files(project: Path):
    contract = Contract(objective="x", constraints={"scope": {"max_files": 1}})
    oracle = freeze(project, contract.api_scope)
    write(project, "src/db.py", "def connect():\n    return False\n")
    write(project, "src/extra.py", "x = 1\n")
    found = checks.check_scope(contract, oracle, state_of(project, contract))
    assert found[0].identifier == "max_files"


def test_scope_budget_on_lines(project: Path):
    contract = Contract(objective="x", constraints={"scope": {"max_lines": 3}})
    oracle = freeze(project, contract.api_scope)
    write(project, "src/extra.py", "\n".join(f"x{i} = {i}" for i in range(20)))
    found = checks.check_scope(contract, oracle, state_of(project, contract))
    assert found[0].identifier == "max_lines"


def test_zero_budget_means_no_budget(governed):
    project, contract, oracle = governed
    write(project, "src/extra.py", "\n".join(f"x{i} = {i}" for i in range(200)))
    assert checks.check_scope(contract, oracle, state_of(project, contract)) == []


def test_changed_files_counts_creations_edits_and_deletions(governed):
    project, contract, oracle = governed
    write(project, "src/db.py", "def connect():\n    return False\n")
    write(project, "src/new.py", "y = 2\n")
    (project / "src" / "auth" / "__init__.py").unlink()

    changed, churn = checks.changed_files(oracle, state_of(project, contract))
    assert set(changed) == {"src/db.py", "src/new.py", "src/auth/__init__.py"}
    assert churn > 0


# -------------------------------------------------------------------- unknown


def test_new_unparseable_file_reports_unknown(governed):
    project, contract, oracle = governed
    write(project, "src/broken.py", "def (:\n")
    found = checks.check_unknown(contract, oracle, state_of(project, contract))
    assert found[0].kind == checks.UNKNOWN_KIND
    assert found[0].identifier == "src/broken.py"


def test_already_unknown_at_baseline_is_not_re_reported(project: Path):
    write(project, "src/broken.py", "def (:\n")
    contract = default_contract("x")
    oracle = freeze(project, contract.api_scope)
    assert checks.check_unknown(contract, oracle, state_of(project, contract)) == []


def test_read_tree_mirrors_freeze_on_an_untouched_repo(governed):
    project, contract, oracle = governed
    state = state_of(project, contract)
    assert state.files == oracle.files
    assert state.api == oracle.api
    assert state.module_graph == oracle.module_graph
