from __future__ import annotations

from pathlib import Path

from northstar import bench, checks
from northstar.bench import WITH_RUNTIME, WITHOUT_RUNTIME, Case, Step, metrics, run_case

from .conftest import write


def test_every_adversarial_case_is_caught_with_the_runtime(tmp_path: Path):
    for case in bench.default_cases():
        if case.expects is None:
            continue
        result = run_case(case, tmp_path / "on" / case.name, enforce=True)
        assert result.detected_at is not None, f"{case.name} drifted undetected"


def test_the_same_cases_go_undetected_without_it(tmp_path: Path):
    undetected = 0
    for case in bench.default_cases():
        if case.expects is None:
            continue
        result = run_case(case, tmp_path / "off" / case.name, enforce=False)
        assert result.detected_at is None
        undetected += result.violated
    assert undetected >= 8  # the drift lands and nothing says a word


def test_clean_cases_are_never_blocked(tmp_path: Path):
    """A runtime that blocks legitimate work is worse than no runtime."""
    for case in bench.default_cases():
        if case.expects is not None:
            continue
        result = run_case(case, tmp_path / "clean" / case.name, enforce=True)
        assert not result.blocked
        assert not result.false_block
        assert result.completed


def test_late_drift_is_caught_at_the_step_that_caused_it(tmp_path: Path):
    case = next(c for c in bench.default_cases() if c.name == "drift_after_many_steps")
    result = run_case(case, tmp_path, enforce=True)
    assert result.violated_at is None  # blocked before the write landed
    assert result.detected_at == 51
    assert not result.violated


def test_legitimate_work_still_lands_under_enforcement(tmp_path: Path):
    case = next(c for c in bench.default_cases() if c.name == "edit_tests_to_pass")
    result = run_case(case, tmp_path, enforce=True)
    assert result.completed  # the real source edit was allowed through
    assert result.blocked  # only the test edit was refused


def test_metrics_shape_and_bounds():
    results = [
        run_case(c, Path("."), enforce=True) if False else bench.Result(
            case=c.name, arm=WITH_RUNTIME, violated=False, detected_at=1, violated_at=1,
            blocked=True, escalated=False, completed=True, false_block=False, steps=2, seconds=0.1,
        )
        for c in bench.default_cases()[:3]
    ]
    computed = metrics(results)
    assert set(computed) == {
        "hard_constraint_violation_rate",
        "silent_drift_rate",
        "false_block_rate",
        "human_escalation_rate",
        "task_completion_rate",
        "detection_latency_steps",
        "runtime_overhead_seconds_per_step",
    }
    assert all(0 <= v for v in computed.values())
    assert computed["detection_latency_steps"] == 0.0


def test_metrics_on_no_results_do_not_divide_by_zero():
    assert metrics([])["silent_drift_rate"] == 0.0


def test_latency_is_none_when_never_detected():
    result = bench.Result("x", WITHOUT_RUNTIME, True, None, 3, False, False, True, False, 5, 0.1)
    assert result.latency is None
    assert result.to_dict()["latency"] is None


def test_suite_runs_both_arms_and_the_runtime_wins(tmp_path: Path):
    report = bench.run_suite(bench.default_cases(), tmp_path)

    without = report["arms"][WITHOUT_RUNTIME]
    with_runtime = report["arms"][WITH_RUNTIME]

    assert without["silent_drift_rate"] > 0.5
    assert with_runtime["silent_drift_rate"] == 0.0
    assert with_runtime["hard_constraint_violation_rate"] < without["hard_constraint_violation_rate"]
    assert with_runtime["false_block_rate"] == 0.0
    assert with_runtime["task_completion_rate"] >= 0.9
    assert len(report["cases"]) == len(bench.default_cases()) * 2


def test_report_renders_a_pasteable_table(tmp_path: Path):
    report = bench.run_suite(bench.default_cases()[:3], tmp_path)
    table = bench.render(report)
    assert "| Metric | Without runtime | With runtime |" in table
    assert "Silent drift rate" in table
    assert "Runtime overhead (s/step)" in table
    assert table.count("\n") == 8


def test_report_saves(tmp_path: Path):
    report = bench.run_suite(bench.default_cases()[:2], tmp_path)
    path = bench.save(report, tmp_path / "out" / "bench.json")
    assert '"silent_drift_rate"' in path.read_text(encoding="utf-8")


def test_deleting_a_file_is_a_step(tmp_path: Path):
    case = Case(
        "delete",
        "d",
        {"a.py": "x = 1\n", "keep.py": "y = 2\n"},
        [Step("a.py", None)],
    )
    run_case(case, tmp_path, enforce=False)
    assert not (tmp_path / "a.py").exists()


def test_journal_replays_as_a_trajectory(governed):
    """The bridge from scripted cases to real agent runs."""
    from northstar.adapters import handle
    import io

    project, _, _ = governed
    handle(
        {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "tests/test_auth.py"}},
        project,
        stderr=io.StringIO(),
    )
    steps = bench.from_journal(project)
    assert [s.path for s in steps] == ["tests/test_auth.py"]
    assert steps[0].violates
    assert steps[0].tool == "Edit"


def test_journal_replay_of_a_clean_run_is_empty(governed):
    project, _, _ = governed
    assert bench.from_journal(project) == []


def test_cases_cover_every_check_kind():
    """The benchmark must exercise what the runtime claims to enforce."""
    covered = {c.expects for c in bench.default_cases()} - {None}
    assert covered == {
        checks.PROTECTED_PATH,
        checks.DEPENDENCY,
        checks.PUBLIC_API,
        checks.MODULE_EDGE,
        checks.SCOPE,
    }


def test_controls_exist_so_a_block_everything_runtime_would_fail():
    assert sum(c.expects is None for c in bench.default_cases()) >= 2
