from __future__ import annotations

from pathlib import Path

from northstar import checks, evidence, policy
from northstar.contract import default_contract
from northstar.policy import Decision, Judgement, Verdict

from .conftest import write


def verdict(decision: Decision) -> Verdict:
    return Verdict(decision, [Judgement(decision, "because", checks.Finding("public_api", "m::f", "d"))])


def test_journal_is_empty_before_anything_happens(tmp_path: Path):
    assert evidence.read_journal(tmp_path) == []
    assert evidence.next_step(tmp_path) == 1


def test_records_append_and_number_monotonically(tmp_path: Path):
    evidence.record(tmp_path, "gate", "Edit", verdict(Decision.DENY))
    evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))

    entries = evidence.read_journal(tmp_path)
    assert [e.step for e in entries] == [1, 2]
    assert entries[0].decision == "DENY"
    assert entries[0].detail["judgements"][0]["finding"]["identifier"] == "m::f"
    assert evidence.next_step(tmp_path) == 3


def test_a_corrupt_line_does_not_blind_the_journal(tmp_path: Path):
    evidence.record(tmp_path, "gate", "Edit", verdict(Decision.ALLOW))
    with evidence.journal_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n\n")
    evidence.record(tmp_path, "gate", "Edit", verdict(Decision.DENY))
    assert len(evidence.read_journal(tmp_path)) == 2


def test_amendments_are_journalled_too(tmp_path: Path):
    evidence.record_amendment(tmp_path, "agreed", ["dependency:httpx"], 2)
    entry = evidence.read_journal(tmp_path)[0]
    assert entry.phase == "amend"
    assert entry.decision == "SIGNED"
    assert entry.detail["grants"] == ["dependency:httpx"]


# ------------------------------------------------------------- wasted steps


def test_wasted_steps_is_zero_on_a_clean_run(tmp_path: Path):
    for _ in range(3):
        evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))
    assert evidence.wasted_steps(evidence.read_journal(tmp_path)) == 0


def test_wasted_steps_counts_the_gap_between_divergence_and_block(tmp_path: Path):
    evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))
    evidence.record(tmp_path, "check", "cli", verdict(Decision.WARN_DRIFT))  # step 2
    evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))
    evidence.record(tmp_path, "check", "cli", verdict(Decision.DENY))  # step 4
    assert evidence.wasted_steps(evidence.read_journal(tmp_path)) == 2


def test_undetected_drift_burns_the_whole_tail(tmp_path: Path):
    evidence.record(tmp_path, "check", "cli", verdict(Decision.UNKNOWN))  # step 1
    for _ in range(4):
        evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))
    assert evidence.wasted_steps(evidence.read_journal(tmp_path)) == 4


def test_amendment_entries_do_not_count_as_divergence(tmp_path: Path):
    evidence.record_amendment(tmp_path, "agreed", ["dependency:httpx"], 2)
    evidence.record(tmp_path, "check", "cli", verdict(Decision.ALLOW))
    assert evidence.wasted_steps(evidence.read_journal(tmp_path)) == 0


# ------------------------------------------------------------------ receipt


def test_receipt_binds_contract_baseline_and_outcome(governed):
    project, contract, oracle = governed
    contract.amend("agreed", ["dependency:httpx"])
    evidence.record_amendment(project, "agreed", ["dependency:httpx"], 2)
    evidence.record(project, "gate", "Edit", verdict(Decision.DENY))

    final = policy.evaluate(contract, oracle, project)
    receipt = evidence.build_receipt(project, contract, oracle, final)

    assert receipt["objective"] == contract.objective
    assert receipt["contract_version"] == 2
    assert receipt["baseline_created"] == oracle.created
    assert receipt["amendments"][0]["grants"] == ["dependency:httpx"]
    assert receipt["metrics"]["steps"] == 2
    assert receipt["metrics"]["decisions"]["DENY"] == 1
    assert receipt["final_verdict"]["decision"] == "ALLOW"


def test_receipt_records_uncovered_files(project: Path):
    from northstar.freeze import freeze

    write(project, "src/broken.py", "def (:\n")
    contract = default_contract("x")
    oracle = freeze(project, contract.api_scope)
    receipt = evidence.build_receipt(project, contract, oracle, Verdict(Decision.ALLOW))
    assert receipt["uncovered_files"] == ["src/broken.py"]
    assert receipt["metrics"]["steps"] == 0


def test_write_receipt(tmp_path: Path):
    path = evidence.write_receipt(tmp_path, {"objective": "x"})
    assert path == evidence.receipt_path(tmp_path)
    assert '"objective": "x"' in path.read_text(encoding="utf-8")
