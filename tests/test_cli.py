from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from northstar import cli
from northstar.authority import Authority, IntegrityError
from northstar.contract import Contract
from northstar.util import read_text

from .conftest import APPROVAL_SECRET, SERVICE, write


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out=out), out.getvalue()


@pytest.fixture
def initialised(project: Path) -> Path:
    code, _ = run(["--root", str(project), "init", "refactor authentication"])
    assert code == cli.EXIT_OK
    return project


# -------------------------------------------------------------------- init


def test_init_creates_contract_baseline_and_wiring(project: Path):
    code, text = run(["--root", str(project), "init", "refactor authentication"])

    assert code == cli.EXIT_OK
    assert (project / ".northstar" / "contract.yaml").exists()
    assert (project / ".northstar" / "oracle.json").exists()
    assert (project / ".claude" / "settings.json").exists()
    assert (project / "AGENTS.md").exists()
    assert "refactor authentication" in text
    assert "baseline frozen" in text
    assert "public symbols" in text


def test_init_can_skip_wiring(project: Path):
    run(["--root", str(project), "init", "x", "--no-install"])
    assert not (project / ".claude").exists()


def test_init_targets_a_single_agent(project: Path):
    run(["--root", str(project), "init", "x", "--agent", "claude"])
    assert not (project / ".codex").exists()


def test_init_reports_uncovered_files(project: Path):
    write(project, "src/broken.py", "def (:\n")
    _, text = run(["--root", str(project), "init", "x", "--no-install"])
    assert "UNKNOWN: 1 file(s)" in text


# ------------------------------------------------------------------- check


def test_check_is_clean_after_init(initialised: Path):
    code, text = run(["--root", str(initialised), "check"])
    assert code == cli.EXIT_OK
    assert "on course" in text


def test_check_blocks_and_explains(initialised: Path):
    write(initialised, "tests/test_auth.py", "# gutted\n")
    code, text = run(["--root", str(initialised), "check"])
    assert code == cli.EXIT_BLOCKED
    assert "[DENY]" in text
    assert "grant needed: protected_path:tests/test_auth.py" in text


def test_check_json(initialised: Path):
    code, text = run(["--root", str(initialised), "check", "--json"])
    assert code == cli.EXIT_OK
    assert json.loads(text)["decision"] == "ALLOW"


# ------------------------------------------------------------------ status


def test_status_restates_the_objective(initialised: Path):
    code, text = run(["--root", str(initialised), "status"])
    assert code == cli.EXIT_OK
    assert 'objective: "refactor authentication"' in text
    assert "contract:  v1" in text
    assert "changed:   0 file(s)" in text
    assert "verdict:   ALLOW" in text


def test_status_shows_drift_and_exits_non_zero(initialised: Path):
    write(initialised, "src/auth/service.py", SERVICE.replace("password: str", "pwd: str"))
    code, text = run(["--root", str(initialised), "status"])
    assert code == cli.EXIT_BLOCKED
    assert "changed:   1 file(s)" in text
    assert "public_api" in text


# ------------------------------------------------------------------- amend


def test_request_then_separate_approval_unblocks_only_what_was_signed(initialised: Path, monkeypatch):
    write(initialised, "pyproject.toml", '[project]\ndependencies = ["requests", "pyyaml", "httpx"]\n')
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_BLOCKED

    code, text = run(
        ["--root", str(initialised), "request", "--grant", "dependency:httpx", "--reason", "async client needed"]
    )
    assert code == cli.EXIT_OK
    assert "contract unchanged" in text
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_BLOCKED
    request_id = text.split("approval request created: ", 1)[1].splitlines()[0]
    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: APPROVAL_SECRET)
    code, text = run(["--root", str(initialised), "approve", request_id])
    assert code == cli.EXIT_OK
    assert "contract v2" in text and "granted: dependency:httpx" in text
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_OK

    # a second, unsigned dependency is still blocked
    write(initialised, "pyproject.toml", '[project]\ndependencies = ["requests", "pyyaml", "httpx", "boto3"]\n')
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_BLOCKED


def test_approval_authenticates_the_signer_reason_and_nonce(initialised: Path, monkeypatch):
    _, text = run(
        ["--root", str(initialised), "amend", "--grant", "public_api:src/auth/service.py::login", "--reason", "mfa argument agreed"]
    )
    request_id = text.split("approval request created: ", 1)[1].splitlines()[0]
    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: APPROVAL_SECRET)
    run(["--root", str(initialised), "approve", request_id])
    from northstar.authority import Authority

    authority = Authority.open(initialised, required=True)
    assert authority is not None
    contract, _ = authority.load()
    assert contract.amendments[0].signed_by
    assert contract.amendments[0].reason == "mfa argument agreed"
    assert contract.amendments[0].approval_id == request_id
    assert contract.amendments[0].signature


# ----------------------------------------------------------- freeze / show


def test_freeze_rebaselines_only_after_human_confirmation(initialised: Path, monkeypatch):
    write(initialised, "src/auth/service.py", SERVICE.replace("password: str", "pwd: str"))
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_BLOCKED

    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: APPROVAL_SECRET)
    code, text = run(["--root", str(initialised), "freeze", "--reason", "new scope agreed"])
    assert code == cli.EXIT_OK and "re-frozen" in text
    assert run(["--root", str(initialised), "check"])[0] == cli.EXIT_OK


def test_show_prints_the_contract(initialised: Path):
    _, text = run(["--root", str(initialised), "show"])
    assert "objective: refactor authentication" in text


# ----------------------------------------------------------------- receipt


def test_receipt_is_written_and_summarised(initialised: Path):
    run(["--root", str(initialised), "check"])
    code, text = run(["--root", str(initialised), "receipt"])
    assert code == cli.EXIT_OK
    assert "wasted steps" in text

    data = json.loads(read_text(initialised / ".northstar" / "receipt.json"))
    assert data["objective"] == "refactor authentication"
    assert data["metrics"]["steps"] >= 1


def test_receipt_json(initialised: Path):
    code, text = run(["--root", str(initialised), "receipt", "--json"])
    assert code == cli.EXIT_OK
    assert json.loads(text)["contract_version"] == 1


def test_live_bench_cli_dispatches_the_reproducible_pipeline(tmp_path: Path, monkeypatch):
    study = SimpleNamespace(id="study", tasks=[object()], agents=[object()])
    monkeypatch.setattr(cli.livebench, "load", lambda path: study)
    monkeypatch.setattr(cli.livebench, "build_plan", lambda value: [{}, {}])
    monkeypatch.setattr(cli.livebench, "save_plan", lambda value, path: path)
    observed: dict[str, object] = {}

    def fake_run(value, path, resume=False):
        observed["resume"] = resume
        return path

    monkeypatch.setattr(cli.livebench, "run", fake_run)
    monkeypatch.setattr(
        cli.livebench,
        "make_packets",
        lambda runs, packets, mapping: (packets, mapping),
    )
    report = {"study_id": "study", "runs": 2}
    monkeypatch.setattr(cli.livebench, "analyse", lambda runs, annotations, mapping: report)
    monkeypatch.setattr(cli.livebench, "save_report", lambda value, path: path)

    code, text = run(["live-bench", "validate", "study.yml"])
    assert code == cli.EXIT_OK and json.loads(text)["pairs"] == 1
    code, text = run(
        ["live-bench", "plan", "study.yml", "--output", str(tmp_path / "plan.json")]
    )
    assert code == cli.EXIT_OK and "plan written" in text
    code, text = run(
        [
            "live-bench",
            "run",
            "study.yml",
            "--output",
            str(tmp_path / "runs"),
            "--resume",
        ]
    )
    assert code == cli.EXIT_OK and observed["resume"] is True
    code, text = run(
        [
            "live-bench",
            "packet",
            str(tmp_path / "runs"),
            "--output",
            str(tmp_path / "packets"),
            "--map",
            str(tmp_path / "map.json"),
        ]
    )
    assert code == cli.EXIT_OK and "private blinding map" in text
    code, text = run(
        [
            "live-bench",
            "analyze",
            str(tmp_path / "runs"),
            "--annotations",
            str(tmp_path / "annotations"),
            "--map",
            str(tmp_path / "map.json"),
            "--output",
            str(tmp_path / "report.json"),
        ]
    )
    assert code == cli.EXIT_OK and json.loads(text) == report


def test_live_bench_cli_reports_study_errors(monkeypatch, capsys):
    def fail(path):
        raise cli.livebench.StudyError("invalid study")

    monkeypatch.setattr(cli.livebench, "load", fail)
    code, _ = run(["live-bench", "validate", "study.yml"])
    assert code == cli.EXIT_ERROR
    assert "invalid study" in capsys.readouterr().err


# -------------------------------------------------------------------- misc


def test_install_command(project: Path):
    code, text = run(["--root", str(project), "install", "--agent", "codex"])
    assert code == cli.EXIT_OK and "wired" in text


def test_governed_wiring_repair_requires_the_approval_secret(initialised: Path, monkeypatch):
    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: "wrong-secret")
    assert run(["--root", str(initialised), "install", "--agent", "claude"])[0] == cli.EXIT_BLOCKED

    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: APPROVAL_SECRET)
    code, text = run(["--root", str(initialised), "install", "--agent", "claude"])
    assert code == cli.EXIT_OK
    assert "settings.json" in text


def test_install_migrates_governed_legacy_codex_notify(initialised: Path, monkeypatch):
    authority = Authority.open(initialised, required=True)
    assert authority is not None
    contract, oracle = authority.load()
    config = write(
        initialised,
        ".codex/config.toml",
        'model = "gpt-5"\nnotify = ["northstar", "--root", "/old/repo", "hook"]\n',
    )
    previous = [initialised / path for path in authority.metadata()["wiring"]]
    legacy_wiring = [
        config if path == initialised / ".codex" / "hooks.json" else path for path in previous
    ]
    authority.persist(contract, oracle, wiring=legacy_wiring)
    (initialised / ".codex" / "hooks.json").unlink()

    with pytest.raises(IntegrityError, match="legacy project-local notify wiring"):
        authority.load()

    monkeypatch.setattr(cli, "interactive_confirmation", lambda request: APPROVAL_SECRET)
    code, text = run(["--root", str(initialised), "install", "--agent", "codex"])

    assert code == cli.EXIT_OK
    assert "hook trust pending" in text
    authority.load()
    wiring = authority.metadata()["wiring"]
    assert ".codex/hooks.json" in wiring
    assert ".codex/config.toml" not in wiring
    assert len(wiring) == len(set(wiring))
    assert "notify" not in read_text(config)


def test_hook_command_reads_stdin(initialised: Path, monkeypatch):
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "tests/test_auth.py"},
            "cwd": str(initialised),
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.stderr", io.StringIO())
    assert run(["hook"])[0] == 2


def test_hook_command_falls_back_to_root_flag(initialised: Path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert run(["--root", str(initialised), "hook"])[0] == 2


def test_bound_hook_root_wins_when_payload_cwd_leaves_repository(
    initialised: Path, tmp_path: Path, monkeypatch
):
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(initialised / ".northstar" / "oracle.json")},
            "cwd": str(tmp_path),
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.stderr", io.StringIO())
    assert run(["--root", str(initialised), "hook"])[0] == 2


def test_missing_contract_is_an_error_not_a_traceback(tmp_path: Path, capsys):
    assert run(["--root", str(tmp_path), "check"])[0] == cli.EXIT_BLOCKED
    assert "not governed" in capsys.readouterr().err


def test_missing_oracle_is_an_error(project: Path, capsys):
    Contract(objective="x").save(project)
    assert run(["--root", str(project), "check"])[0] == cli.EXIT_BLOCKED
    assert "external authority is missing" in capsys.readouterr().err


def test_root_defaults_to_the_nearest_governed_dir(initialised: Path, monkeypatch):
    nested = initialised / "src" / "auth"
    monkeypatch.chdir(nested)
    assert run(["check"])[0] == cli.EXIT_OK


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit):
        cli.main([])


# ----------------------------------------------------------------- compile


TASK = """\
Refactor authentication.
Do not change the public API.
Do not add runtime dependencies.
Preserve Python 3.11 support.
"""


def test_compile_shows_its_workings(project: Path):
    code, text = run(["compile", TASK])
    assert code == cli.EXIT_OK
    assert "objective: Refactor authentication." in text
    assert 'from: "Do not change the public API."' in text
    assert "NOT COMPILED" in text
    assert "compiled" in text and "% of the constraint-like sentences" in text


def test_compile_from_a_file_and_write(project: Path, tmp_path: Path):
    task = tmp_path / "task.md"
    task.write_text(TASK, encoding="utf-8")
    code, text = run(["--root", str(project), "compile", "--file", str(task), "--write"])
    assert code == cli.EXIT_OK
    assert (project / ".northstar" / "contract.yaml").exists()
    saved = Contract.load(project)
    assert saved.constraints["public_api"]["change"] == "forbidden"
    assert saved.constraints["dependencies"]["additions"] == "forbidden"


def test_compile_of_a_fully_understood_task_says_nothing_extra():
    _, text = run(["compile", "Refactor auth.\nDo not change the public API."])
    assert "NOT COMPILED" not in text


def test_init_from_a_task_file(project: Path, tmp_path: Path):
    task = tmp_path / "task.md"
    task.write_text(TASK, encoding="utf-8")
    code, text = run(["--root", str(project), "init", "--from-task", str(task), "--no-install"])
    assert code == cli.EXIT_OK
    assert "NOT COMPILED" in text  # the python 3.11 line, admitted
    assert Contract.load(project).constraints["dependencies"]["additions"] == "forbidden"


def test_init_with_behaviour_freezes_the_suite(project: Path):
    code, _ = run(["--root", str(project), "init", "x", "--behavior", "--no-install"])
    assert code == cli.EXIT_OK
    contract = Contract.load(project)
    assert contract.tracks_behavior


def test_v01_migration_requires_review_and_authenticates_old_amendments(project: Path):
    from northstar.authority import Authority
    from northstar.freeze import freeze

    legacy = Contract(objective="legacy")
    legacy.amend("old exception", ["dependency:httpx"], signed_by="arbitrary-v01-value")
    legacy.save(project)
    freeze(project, legacy.api_scope).save(project)

    assert run(["--root", str(project), "migrate"])[0] == cli.EXIT_BLOCKED
    code, text = run(["--root", str(project), "migrate", "--accept-existing-state"])
    assert code == cli.EXIT_OK
    assert "migrated contract v2" in text
    authority = Authority.open(project, required=True)
    assert authority is not None
    contract, _ = authority.load()
    amendment = contract.amendments[0]
    assert amendment.approval_id == "migration-v2"
    assert amendment.signature
    assert amendment.signed_by != "arbitrary-v01-value"


# ------------------------------------------------------------------- bench


def test_bench_prints_a_table():
    code, text = run(["bench"])
    assert code == cli.EXIT_OK
    assert "| Metric | Without runtime | With runtime |" in text
    assert "Silent drift rate" in text


def test_bench_json_and_output(tmp_path: Path):
    target = tmp_path / "report.json"
    code, text = run(["bench", "--json", "--output", str(target)])
    assert code == cli.EXIT_OK
    report = json.loads(text.split("\nwritten to")[0])
    assert report["arms"]["with_runtime"]["silent_drift_rate"] == 0.0
    assert target.exists()
