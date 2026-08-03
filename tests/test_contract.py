from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from northstar.contract import (
    Amendment,
    Contract,
    ContractError,
    contract_path,
    default_contract,
)


def test_defaults_are_merged_not_replaced():
    c = Contract(objective="x", constraints={"dependencies": {"additions": "allowed"}})
    assert c.constraints["dependencies"]["additions"] == "allowed"
    assert c.constraints["public_api"]["change"] == "forbidden"  # untouched default kept


def test_unknown_section_rejected():
    with pytest.raises(ContractError, match="unknown constraint section"):
        Contract(objective="x", constraints={"nonsense": {}})


def test_objective_required():
    with pytest.raises(ContractError, match="objective"):
        Contract(objective="   ")


@pytest.mark.parametrize(
    "constraints",
    [
        {"public_api": {"change": "maybe"}},
        {"dependencies": {"additions": True}},
        {"module_graph": {"new_edges": None}},
    ],
)
def test_invalid_rule_values_rejected(constraints):
    with pytest.raises(ContractError, match="must be one of"):
        Contract(objective="x", constraints=constraints)


@pytest.mark.parametrize("value", [-1, "5", True, 1.5])
def test_scope_budget_must_be_non_negative_int(value):
    with pytest.raises(ContractError, match="non-negative int"):
        Contract(objective="x", constraints={"scope": {"max_files": value}})


def test_protected_paths_must_be_a_list():
    with pytest.raises(ContractError, match="list of globs"):
        Contract(objective="x", constraints={"protected_paths": "tests/**"})


def test_state_dir_is_always_protected():
    c = Contract(objective="x")
    assert ".northstar/**" in c.protected_paths


def test_version_tracks_amendments():
    c = Contract(objective="x")
    assert c.version == 1
    c.amend("api must move", ["public_api:src/a.py::f"])
    assert c.version == 2


def test_amend_requires_reason_and_grants():
    c = Contract(objective="x")
    with pytest.raises(ContractError, match="reason"):
        c.amend("  ", ["public_api:x"])
    with pytest.raises(ContractError, match="at least one grant"):
        c.amend("because", [])


def test_grant_matches_exactly_and_by_glob():
    c = Contract(objective="x")
    c.amend("scoped", ["public_api:src/auth.py::*"])
    assert c.is_granted("public_api", "src/auth.py::login") is not None
    assert c.is_granted("public_api", "src/other.py::login") is None
    assert c.is_granted("dependency", "src/auth.py::login") is None


def test_grant_is_scoped_not_a_general_amnesty():
    c = Contract(objective="x")
    c.amend("only this symbol", ["public_api:src/a.py::login"])
    assert c.is_granted("public_api", "src/a.py::login") is not None
    assert c.is_granted("public_api", "src/a.py::logout") is None


def test_roundtrip_through_disk(tmp_path: Path):
    c = default_contract("refactor auth")
    c.amend("needed", ["dependency:httpx"], signed_by="asesor")
    path = c.save(tmp_path)
    assert path == contract_path(tmp_path)

    loaded = Contract.load(tmp_path)
    assert loaded.objective == "refactor auth"
    assert loaded.version == 2
    assert loaded.amendments[0].signed_by == "asesor"
    assert loaded.amendments[0].grants == ["dependency:httpx"]
    assert "tests/**" in loaded.protected_paths


def test_load_missing_contract(tmp_path: Path):
    with pytest.raises(ContractError, match="no contract"):
        Contract.load(tmp_path)


def test_load_invalid_yaml(tmp_path: Path):
    path = contract_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("objective: [unclosed\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid YAML"):
        Contract.load(tmp_path)


def test_from_dict_rejects_non_mapping():
    with pytest.raises(ContractError, match="mapping"):
        Contract.from_dict(["nope"])  # type: ignore[arg-type]


def test_amendment_from_dict_rejects_malformed():
    with pytest.raises(ContractError, match="invalid amendment"):
        Amendment.from_dict({"reason": "no version"})


def test_amendment_roundtrip():
    a = Amendment(version=2, reason="r", grants=["dependency:x"])
    assert Amendment.from_dict(a.to_dict()).to_dict() == a.to_dict()


def test_template_is_valid_yaml_and_parses():
    c = default_contract("do the thing")
    assert c.objective == "do the thing"
    assert "tests/**" in c.constraints["protected_paths"]
    assert yaml.safe_load(yaml.safe_dump(c.to_dict()))["version"] == 1


def test_rule_and_api_scope_accessors():
    c = default_contract("x")
    assert c.rule("public_api", "change") == "forbidden"
    assert c.api_scope == ["**/*.py"]
    assert "git push*" in c.forbidden_commands
