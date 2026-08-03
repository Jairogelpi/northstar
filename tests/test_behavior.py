from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from northstar import behavior, checks, policy
from northstar.behavior import FAILED, PASSED, SKIPPED, capture, compare, detect
from northstar.contract import Contract
from northstar.freeze import freeze
from northstar.policy import Decision

from .conftest import write

PYTEST_OUTPUT = """\
==== short test summary info ====
PASSED tests/test_auth.py::test_login
FAILED tests/test_auth.py::test_logout
SKIPPED tests/test_auth.py::test_slow
ERROR tests/test_auth.py::test_broken
"""

GO_OUTPUT = """\
=== RUN   TestLogin
--- PASS: TestLogin (0.00s)
=== RUN   TestLogout
--- FAIL: TestLogout (0.01s)
--- SKIP: TestSlow (0.00s)
"""


# ------------------------------------------------------------------- parsing


def test_parse_pytest_outcomes():
    assert behavior.parse_pytest(PYTEST_OUTPUT) == {
        "tests/test_auth.py::test_login": PASSED,
        "tests/test_auth.py::test_logout": FAILED,
        "tests/test_auth.py::test_slow": SKIPPED,
        "tests/test_auth.py::test_broken": FAILED,  # an error is a failure
    }


def test_parse_go_outcomes():
    assert behavior.parse_go(GO_OUTPUT) == {
        "TestLogin": PASSED,
        "TestLogout": FAILED,
        "TestSlow": SKIPPED,
    }


def test_parse_empty_output():
    assert behavior.parse_pytest("") == {}


# ---------------------------------------------------------------- comparison


def test_a_test_that_starts_failing_is_a_behaviour_change():
    changed = compare({"a": PASSED, "b": PASSED}, {"a": PASSED, "b": FAILED})
    assert changed == [("b", PASSED, FAILED)]


def test_a_test_that_starts_passing_is_also_reported():
    """Whether it is welcome is the human's call, not the runtime's."""
    assert compare({"a": FAILED}, {"a": PASSED}) == [("a", FAILED, PASSED)]


def test_a_disappeared_test_is_reported():
    assert compare({"a": PASSED}, {}) == [("a", PASSED, "missing")]


def test_a_new_test_is_reported():
    assert compare({}, {"b": PASSED}) == [("b", "absent", PASSED)]


def test_identical_outcomes_are_silent():
    assert compare({"a": PASSED}, {"a": PASSED}) == []


# ---------------------------------------------------------------- detection


def test_detect_finds_pytest_in_a_python_project(project: Path):
    assert detect(project)[0] == "pytest"


def test_detect_returns_none_without_a_suite(tmp_path: Path):
    assert detect(tmp_path) is None


def test_capture_without_a_runner_is_an_error_not_an_empty_pass(tmp_path: Path):
    run = capture(tmp_path)
    assert not run.usable
    assert run.error == "no supported test runner found"
    assert run.outcomes == {}


def test_capture_reports_a_missing_binary(tmp_path: Path):
    run = capture(tmp_path, ["definitely-not-a-real-binary-xyz"])
    assert not run.usable
    assert "not installed" in run.error


def test_capture_reports_unparseable_output(tmp_path: Path):
    run = capture(tmp_path, ["python", "-c", "print('hello')"])
    assert not run.usable
    assert run.error == "test output could not be parsed"


def test_capture_reports_a_timeout(tmp_path: Path):
    run = capture(tmp_path, ["python", "-c", "import time; time.sleep(5)"], timeout=1)
    assert not run.usable
    assert "exceeded 1s" in run.error


def test_capture_reads_a_real_suite(tmp_path: Path):
    write(tmp_path, "tests/test_demo.py", "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert False\n")
    run = capture(tmp_path, ["python", "-m", "pytest", "-q", "--no-header", "-rA", str(tmp_path / "tests")])
    assert run.usable
    outcomes = {k.rsplit("::", 1)[-1]: v for k, v in run.outcomes.items()}
    assert outcomes["test_ok"] == PASSED
    assert outcomes["test_bad"] == FAILED


# ----------------------------------------------------------- the check itself


def behaving(root: Path, rule: str = "forbidden") -> tuple[Contract, object]:
    contract = Contract(
        objective="preserve behaviour",
        constraints={"behavior": {"change": rule, "command": [
            "python", "-m", "pytest", "-q", "--no-header", "-rA", "tests",
        ]}},
    )
    oracle = freeze(root, contract.api_scope, capture_behavior=True,
                    behavior_command=contract.behavior_command)
    return contract, oracle


def test_behaviour_is_frozen_at_t0_and_verified_later(tmp_path: Path):
    """The semantic constraint, made deterministic: same suite, same outcomes."""
    write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write(tmp_path, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    contract, oracle = behaving(tmp_path)
    assert oracle.behavior, "baseline outcomes were not captured"
    state = checks.read_tree(tmp_path, contract.api_scope)
    assert checks.check_behavior(contract, oracle, state) == []

    # The signature is untouched, so no API check fires. Only behaviour moved.
    write(tmp_path, "calc.py", "def add(a, b):\n    return a * b\n")
    state = checks.read_tree(tmp_path, contract.api_scope)
    found = checks.check_behavior(contract, oracle, state)
    assert len(found) == 1
    assert found[0].kind == checks.BEHAVIOR
    assert "passed -> failed" in found[0].detail


def test_behaviour_change_verdict_follows_the_rule(tmp_path: Path):
    write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write(tmp_path, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    contract, oracle = behaving(tmp_path)
    write(tmp_path, "calc.py", "def add(a, b):\n    return a * b\n")

    verdict = policy.evaluate(contract, oracle, tmp_path)
    assert verdict.decision is Decision.DENY


def test_behaviour_is_not_checked_unless_the_contract_asks(project: Path):
    """A check people disable is worse than one they opt into."""
    contract = Contract(objective="x")
    assert not contract.tracks_behavior
    oracle = freeze(project, contract.api_scope)
    assert oracle.behavior == {}
    state = checks.read_tree(project, contract.api_scope)
    assert checks.check_behavior(contract, oracle, state) == []


def test_an_unrunnable_baseline_is_unknown_not_clean(tmp_path: Path):
    write(tmp_path, "a.py", "x = 1\n")
    contract = Contract(objective="x", constraints={"behavior": {"change": "forbidden"}})
    oracle = freeze(tmp_path, contract.api_scope, capture_behavior=True)
    assert any("behavior:" in entry for entry in oracle.unknown)


def test_an_unrunnable_recheck_is_unknown_not_unchanged(tmp_path: Path):
    write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    write(tmp_path, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    contract, oracle = behaving(tmp_path)

    oracle.behavior_command = ["definitely-not-a-real-binary-xyz"]
    state = checks.read_tree(tmp_path, contract.api_scope)
    found = checks.check_behavior(contract, oracle, state)
    assert found[0].kind == checks.UNKNOWN_KIND
    assert "unverified, not unchanged" in found[0].detail
