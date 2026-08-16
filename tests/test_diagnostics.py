from __future__ import annotations

from pathlib import Path

from northstar import adapters, diagnostics
from northstar.authority import marker_path


def test_ungoverned_project_is_reported_without_mutation(tmp_path: Path):
    before = set(tmp_path.rglob("*"))
    report = diagnostics.run(tmp_path)

    assert report["overall"] == "degraded"
    assert report["governed"] is False
    assert any(check["name"] == "authority" and check["status"] == "warn" for check in report["checks"])
    assert set(tmp_path.rglob("*")) == before


def test_governed_project_reports_authority_tree_and_hook_activity(governed):
    project, _, _ = governed
    adapters.handle(
        {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(project)},
        project,
    )

    report = diagnostics.run(project)

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["authority"]["status"] == "pass"
    assert checks["tree"]["status"] == "pass"
    assert checks["hook_activity"]["status"] == "pass"


def test_broken_mirror_is_a_failed_diagnostic(governed):
    project, _, _ = governed
    (project / ".northstar" / "oracle.json").write_text("{}", encoding="utf-8")

    report = diagnostics.run(project)

    assert report["overall"] == "broken"
    assert any(check["name"] == "authority" and check["status"] == "fail" for check in report["checks"])


def test_governance_marker_without_authority_fails_closed(tmp_path: Path):
    marker = marker_path(tmp_path)
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    report = diagnostics.run(tmp_path)
    rendered = diagnostics.render(report)

    assert report["overall"] == "broken"
    assert "external authority is missing" in rendered
    assert "next:" in rendered


def test_tree_drift_is_reported_as_broken(governed):
    project, _, _ = governed
    (project / "tests" / "test_auth.py").write_text("changed\n", encoding="utf-8")

    report = diagnostics.run(project)

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["tree"]["status"] == "fail"
    assert report["overall"] == "broken"
