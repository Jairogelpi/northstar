from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

from northstar import authority as authority_mod
from northstar import cli, install
from northstar.authority import (
    Authority,
    IntegrityError,
    authority_home,
    interactive_confirmation,
    marker_path,
    prompt_new_approval_secret,
)
from northstar.contract import ContractError, default_contract
from northstar.freeze import freeze

from .conftest import APPROVAL_SECRET


def test_external_bundle_is_canonical_and_mirrored(governed):
    project, contract, oracle = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    loaded_contract, loaded_oracle = authority.load()

    assert loaded_contract.objective == contract.objective
    assert loaded_oracle.files == oracle.files
    assert authority.contract_path != project / ".northstar" / "contract.yaml"
    assert authority.metadata()["project_id"] == authority.project
    assert authority_home() in authority.path.parents


def test_authority_is_discovered_from_nested_working_directory(governed):
    project, _, _ = governed
    nested = project / "src" / "auth"
    authority = Authority.open(nested, required=True)
    assert authority is not None
    assert authority.root == project


@pytest.mark.parametrize("target", ["contract", "oracle", "marker"])
def test_missing_or_changed_mirror_is_an_integrity_failure(governed, target):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    paths = {
        "contract": project / ".northstar" / "contract.yaml",
        "oracle": project / ".northstar" / "oracle.json",
        "marker": marker_path(project),
    }
    paths[target].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="mirror failed"):
        authority.load()


@pytest.mark.parametrize(
    "name,contents,message",
    [
        ("manifest", b"{broken", "manifest is missing or corrupt"),
        ("metadata", b"[]", "metadata is not an object"),
        ("key", b"short", "invalid length"),
        ("oracle", b"{}", "trusted artifact failed"),
    ],
)
def test_corrupt_external_authority_fails_closed(governed, name, contents, message):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    targets = {
        "manifest": authority.manifest_path,
        "metadata": authority.metadata_path,
        "key": authority.key_path,
        "oracle": authority.oracle_path,
    }
    targets[name].write_bytes(contents)
    with pytest.raises(IntegrityError, match=message):
        authority.load()


def test_manifest_signature_tampering_is_detected(governed):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    manifest = json.loads(authority.manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["oracle.json"] = "0" * 64
    authority.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="signature does not verify"):
        authority.verify()


def test_hook_wiring_is_verified_structurally(project: Path):
    wiring = install.install(project, ["claude"])
    contract = default_contract("x")
    oracle = freeze(project, contract.api_scope)
    authority = Authority.bootstrap(
        project, contract, oracle, wiring, approval_passphrase=APPROVAL_SECRET
    )

    settings = project / ".claude" / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    with pytest.raises(IntegrityError, match="no PreToolUse"):
        authority.load()
    # A human repair can deliberately bypass the broken-wiring check, then reseal.
    repaired = install.install(project, ["claude"])
    live_contract, live_oracle = authority.load(check_wiring=False)
    authority.persist(live_contract, live_oracle, wiring=repaired, check_wiring=False)
    authority.load()


def test_request_is_untrusted_until_consumed_once(governed):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    request = authority.create_request("approved dependency", ["dependency:httpx"])
    assert authority.load()[0].version == 1

    amendment = authority.approve_request(request, lambda data: APPROVAL_SECRET)
    assert amendment.approval_id == request
    assert amendment.signature
    assert authority.load()[0].version == 2
    with pytest.raises(IntegrityError, match="already been consumed"):
        authority.approve_request(request, lambda data: APPROVAL_SECRET)


def test_declined_or_foreign_request_cannot_change_the_contract(governed):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    request = authority.create_request("no", ["dependency:x"])
    with pytest.raises(IntegrityError, match="not confirmed"):
        authority.approve_request(request, lambda data: "")
    assert authority.load()[0].version == 1

    wrong_secret_request = authority.create_request("wrong secret", ["dependency:y"])
    with pytest.raises(IntegrityError, match="passphrase is invalid"):
        authority.approve_request(wrong_secret_request, lambda data: "definitely-wrong")
    assert authority.load()[0].version == 1

    path = authority.path / "requests" / f"{request}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["project_id"] = "someone-else"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(IntegrityError, match="does not belong"):
        authority.approve_request(request, lambda data: APPROVAL_SECRET)


def test_amendment_signature_survives_manifest_resealing(governed):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    request = authority.create_request("real reason", ["dependency:httpx"])
    authority.approve_request(request, lambda data: APPROVAL_SECRET)

    data = yaml.safe_load(authority.contract_path.read_text(encoding="utf-8"))
    data["amendments"][0]["reason"] = "forged reason"
    forged = yaml.safe_dump(data, sort_keys=False).encode("utf-8")
    authority.contract_path.write_bytes(forged)
    (project / ".northstar" / "contract.yaml").write_bytes(forged)
    authority.seal()  # Even a valid bundle seal cannot forge the approval chain.
    with pytest.raises(IntegrityError, match="amendment v2 signature"):
        authority.load()


def test_request_validation_and_missing_request(governed):
    project, _, _ = governed
    authority = Authority.open(project, required=True)
    assert authority is not None
    with pytest.raises(ContractError, match="needs a reason"):
        authority.create_request("", [])
    with pytest.raises(IntegrityError, match="missing or corrupt"):
        authority.approve_request("not-there", lambda data: APPROVAL_SECRET)


def test_interactive_approval_rejects_non_tty():
    with pytest.raises(IntegrityError, match="interactive terminal"):
        interactive_confirmation({"reason": "x", "grants": ["dependency:x"]})


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_interactive_secret_creation_and_approval(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys, "stdout", _TTY())
    answers = iter([APPROVAL_SECRET, APPROVAL_SECRET, APPROVAL_SECRET])
    monkeypatch.setattr(authority_mod.getpass, "getpass", lambda prompt: next(answers))
    assert prompt_new_approval_secret() == APPROVAL_SECRET
    assert interactive_confirmation({"reason": "x", "grants": ["dependency:x"]}) == APPROVAL_SECRET


@pytest.mark.parametrize(
    "answers,message",
    [(["one-long-secret", "different-secret"], "do not match"), (["short", "short"], "12 characters")],
)
def test_invalid_new_approval_secret_is_rejected(monkeypatch, answers, message):
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys, "stdout", _TTY())
    values = iter(answers)
    monkeypatch.setattr(authority_mod.getpass, "getpass", lambda prompt: next(values))
    with pytest.raises(IntegrityError, match=message):
        prompt_new_approval_secret()


def test_marker_without_external_state_fails_closed(project: Path):
    marker_path(project).parent.mkdir(parents=True)
    marker_path(project).write_text("{}", encoding="utf-8")
    with pytest.raises(IntegrityError, match="external authority is missing"):
        Authority.open(project)


def test_bootstrap_rejects_weak_secret_before_writing(project: Path):
    contract = default_contract("x")
    oracle = freeze(project, contract.api_scope)
    target = Authority.for_root(project)
    with pytest.raises(IntegrityError, match="12 characters"):
        Authority.bootstrap(project, contract, oracle, approval_passphrase="short")
    assert not target.path.exists()


def test_bootstrap_cannot_silently_replace_existing_authority(governed):
    project, contract, oracle = governed
    with pytest.raises(IntegrityError, match="already exists"):
        Authority.bootstrap(project, contract, oracle)


def test_cli_approval_refuses_noninteractive_use(project: Path):
    assert cli_run(["--root", str(project), "init", "x", "--no-install"])[0] == cli.EXIT_OK
    _, text = cli_run(
        ["--root", str(project), "request", "--grant", "dependency:x", "--reason", "x"]
    )
    request = text.split("approval request created: ", 1)[1].splitlines()[0]
    code, _ = cli_run(["--root", str(project), "approve", request])
    assert code == cli.EXIT_BLOCKED


def cli_run(argv: list[str]) -> tuple[int, str]:
    import io

    output = io.StringIO()
    return cli.main(argv, out=output), output.getvalue()
