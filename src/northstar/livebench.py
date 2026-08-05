"""Reproducible, independently labelled live-agent evaluation.

This module deliberately separates execution from judgement.  Northstar prepares
paired workspaces and records complete artifacts; humans label constraint outcomes
from blinded packets.  Product findings are process evidence, never ground truth.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import install as install_mod
from .authority import Authority, IntegrityError
from .contract import Contract, ContractError
from .freeze import freeze

SCHEMA = 1
WITH_RUNTIME = "with_runtime"
WITHOUT_RUNTIME = "without_runtime"
ARMS = (WITHOUT_RUNTIME, WITH_RUNTIME)
PLACEHOLDERS = {
    "prompt",
    "prompt_file",
    "workspace",
    "native_trace",
    "python",
    "model",
}
INSTRUMENTATION_PATHS = {
    ".northstar",
    ".claude/settings.json",
    ".codex/hooks.json",
    "AGENTS.md",
    "CLAUDE.md",
}


class StudyError(RuntimeError):
    """The study cannot be executed or analysed without guessing."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for char in text):
        raise StudyError(f"{field} must be a non-empty portable identifier")
    return text


def _string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StudyError(f"{field} must be non-empty")
    return text


def _reject_unknown(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise StudyError(f"{field} contains unknown field {unknown[0]!r}")


def _commands(value: Any, field: str) -> list[list[str]]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or any(not isinstance(item, list) for item in value):
        raise StudyError(f"{field} must be a list of argv lists, never shell text")
    commands: list[list[str]] = []
    for index, item in enumerate(value):
        if not item or any(not isinstance(part, str) or not part for part in item):
            raise StudyError(f"{field}[{index}] must contain non-empty strings")
        commands.append(list(item))
    return commands


def _argv(value: Any, field: str) -> list[str]:
    commands = _commands([value] if isinstance(value, list) else value, field)
    if len(commands) != 1:
        raise StudyError(f"{field} must be one argv list")
    return commands[0]


@dataclass(frozen=True)
class Constraint:
    id: str
    statement: str


@dataclass(frozen=True)
class Task:
    id: str
    objective: str
    repository_url: str
    repository_commit: str
    hard_constraints: tuple[Constraint, ...]
    northstar_contract: dict[str, Any]
    setup: tuple[tuple[str, ...], ...]
    test: tuple[str, ...]


@dataclass(frozen=True)
class Agent:
    id: str
    host: str
    version: str
    model: str
    command: tuple[str, ...]
    version_command: tuple[str, ...]


@dataclass(frozen=True)
class Study:
    id: str
    seed: int
    repetitions: int
    capture_contents: bool
    timeout_seconds: float
    tasks: tuple[Task, ...]
    agents: tuple[Agent, ...]


def load(path: Path) -> Study:
    """Load and strictly validate one study manifest."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise StudyError(f"cannot load study manifest: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise StudyError(f"study schema must be {SCHEMA}")
    _reject_unknown(
        raw,
        {
            "schema",
            "study_id",
            "seed",
            "repetitions",
            "capture_contents",
            "timeout_seconds",
            "tasks",
            "agents",
        },
        "study",
    )
    study_id = _identifier(raw.get("study_id"), "study_id")
    seed = raw.get("seed", 0)
    repetitions = raw.get("repetitions", 0)
    timeout_seconds = raw.get("timeout_seconds", 3600)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise StudyError("seed must be an integer")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise StudyError("repetitions must be a positive integer")
    if not isinstance(raw.get("capture_contents", False), bool):
        raise StudyError("capture_contents must be true or false")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise StudyError("timeout_seconds must be a positive number")

    tasks_raw = raw.get("tasks")
    agents_raw = raw.get("agents")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise StudyError("tasks must contain at least one task")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise StudyError("agents must contain at least one agent")

    tasks: list[Task] = []
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise StudyError(f"tasks[{index}] must be an object")
        _reject_unknown(
            item,
            {
                "id",
                "objective",
                "repository",
                "hard_constraints",
                "northstar_contract",
                "setup",
                "test",
            },
            f"tasks[{index}]",
        )
        task_id = _identifier(item.get("id"), f"tasks[{index}].id")
        repository = item.get("repository")
        if not isinstance(repository, dict):
            raise StudyError(f"tasks[{index}].repository must be an object")
        _reject_unknown(repository, {"url", "commit"}, f"tasks[{index}].repository")
        constraints_raw = item.get("hard_constraints")
        if not isinstance(constraints_raw, list) or not constraints_raw:
            raise StudyError(f"tasks[{index}].hard_constraints must not be empty")
        constraints: list[Constraint] = []
        for c_index, constraint in enumerate(constraints_raw):
            if not isinstance(constraint, dict):
                raise StudyError(f"tasks[{index}].hard_constraints[{c_index}] must be an object")
            _reject_unknown(
                constraint,
                {"id", "statement"},
                f"tasks[{index}].hard_constraints[{c_index}]",
            )
            constraints.append(
                Constraint(
                    _identifier(constraint.get("id"), "constraint id"),
                    _string(constraint.get("statement"), "constraint statement"),
                )
            )
        if len({constraint.id for constraint in constraints}) != len(constraints):
            raise StudyError(f"task {task_id} has duplicate constraint ids")
        northstar_contract = item.get("northstar_contract", {})
        if not isinstance(northstar_contract, dict):
            raise StudyError(f"task {task_id}.northstar_contract must be an object")
        try:
            Contract(_string(item.get("objective"), "task objective"), constraints=northstar_contract)
        except ContractError as exc:
            raise StudyError(f"task {task_id} has an invalid Northstar contract: {exc}") from exc
        setup = _commands(item.get("setup", []), f"task {task_id}.setup")
        test = _argv(item.get("test"), f"task {task_id}.test")
        for c_index, setup_command in enumerate(setup):
            _validate_placeholders(setup_command, f"task {task_id}.setup[{c_index}]")
        _validate_placeholders(test, f"task {task_id}.test")
        tasks.append(
            Task(
                task_id,
                _string(item.get("objective"), "task objective"),
                _string(repository.get("url"), "repository.url"),
                _string(repository.get("commit"), "repository.commit"),
                tuple(constraints),
                northstar_contract,
                tuple(tuple(command) for command in setup),
                tuple(test),
            )
        )

    agents: list[Agent] = []
    for index, item in enumerate(agents_raw):
        if not isinstance(item, dict):
            raise StudyError(f"agents[{index}] must be an object")
        _reject_unknown(
            item,
            {"id", "host", "version", "model", "version_command", "command"},
            f"agents[{index}]",
        )
        agent_id = _identifier(item.get("id"), f"agents[{index}].id")
        command = _argv(item.get("command"), f"agent {agent_id}.command")
        version_command = _argv(
            item.get("version_command"), f"agent {agent_id}.version_command"
        )
        joined = "\0".join(command)
        if "{prompt}" not in joined and "{prompt_file}" not in joined:
            raise StudyError(f"agent {agent_id}.command must consume {{prompt}} or {{prompt_file}}")
        _validate_placeholders(command, f"agent {agent_id}.command")
        _validate_placeholders(version_command, f"agent {agent_id}.version_command")
        host = _string(item.get("host"), "agent host")
        _agent_target(host)
        agents.append(
            Agent(
                agent_id,
                host,
                _string(item.get("version"), "agent version"),
                _string(item.get("model"), "agent model"),
                tuple(command),
                tuple(version_command),
            )
        )
    if len({task.id for task in tasks}) != len(tasks):
        raise StudyError("task ids must be unique")
    if len({agent.id for agent in agents}) != len(agents):
        raise StudyError("agent ids must be unique")
    return Study(
        study_id,
        seed,
        repetitions,
        bool(raw.get("capture_contents", False)),
        float(timeout_seconds),
        tuple(tasks),
        tuple(agents),
    )


def _validate_placeholders(parts: list[str], field: str) -> None:
    for part in parts:
        cursor = 0
        while cursor < len(part):
            if part[cursor] == "}":
                raise StudyError(f"{field} contains an unmatched '}}'")
            if part[cursor] != "{":
                cursor += 1
                continue
            start = cursor
            try:
                end = part.index("}", start)
            except ValueError as exc:
                raise StudyError(f"{field} contains an unmatched '{{'") from exc
            name = part[start + 1 : end]
            if name not in PLACEHOLDERS:
                raise StudyError(f"{field} uses unsupported placeholder {{{name}}}")
            cursor = end + 1


def _study_sha256(study: Study) -> str:
    payload = json.dumps(asdict(study), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def build_plan(study: Study) -> list[dict[str, Any]]:
    """Return a deterministic paired plan with randomised arm order."""
    pairs: list[list[dict[str, Any]]] = []
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"northstar-livebench:{study.id}")
    for task in study.tasks:
        for agent in study.agents:
            for repetition in range(1, study.repetitions + 1):
                pair_id = f"{task.id}__{agent.id}__r{repetition:03d}"
                pair = []
                for arm in ARMS:
                    run_id = str(uuid.uuid5(namespace, f"{pair_id}:{arm}"))
                    pair.append(
                        {
                            "run_id": run_id,
                            "pair_id": pair_id,
                            "task_id": task.id,
                            "agent_id": agent.id,
                            "repetition": repetition,
                            "arm": arm,
                        }
                    )
                pairs.append(pair)
    rng = random.Random(study.seed)
    rng.shuffle(pairs)
    ordered: list[dict[str, Any]] = []
    for pair in pairs:
        rng.shuffle(pair)
        ordered.extend(pair)
    for sequence, run in enumerate(ordered, start=1):
        run["sequence"] = sequence
    return ordered


def save_plan(study: Study, path: Path) -> Path:
    data = {
        "schema": SCHEMA,
        "study_id": study.id,
        "study_sha256": _study_sha256(study),
        "seed": study.seed,
        "repetitions": study.repetitions,
        "runs": build_plan(study),
    }
    return _write_json(path, data)


def _expand(parts: tuple[str, ...] | list[str], values: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for part in parts:
        value = part
        for name, replacement in values.items():
            value = value.replace("{" + name + "}", replacement)
        expanded.append(value)
    return expanded


def _run_command(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired:
        with stderr_path.open("ab") as stderr:
            stderr.write(f"command timed out after {timeout_seconds:g} seconds\n".encode())
        return 124, time.perf_counter() - started
    except OSError as exc:
        stderr_path.write_text(str(exc) + "\n", encoding="utf-8")
        return 127, time.perf_counter() - started
    return completed.returncode, time.perf_counter() - started


def _clone(
    task: Task,
    workspace: Path,
    run_dir: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> None:
    code, _ = _run_command(
        ["git", "clone", "--no-checkout", "--", task.repository_url, str(workspace)],
        run_dir,
        env,
        run_dir / "clone.stdout.log",
        run_dir / "clone.stderr.log",
        timeout_seconds,
    )
    if code:
        raise StudyError(f"git clone failed for task {task.id}; see {run_dir / 'clone.stderr.log'}")
    code, _ = _run_command(
        ["git", "-c", "advice.detachedHead=false", "checkout", "--detach", task.repository_commit],
        workspace,
        env,
        run_dir / "checkout.stdout.log",
        run_dir / "checkout.stderr.log",
        timeout_seconds,
    )
    if code:
        raise StudyError(f"git checkout failed for task {task.id}; see {run_dir / 'checkout.stderr.log'}")


def _snapshot(root: Path, capture_contents: bool) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            files.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        item: dict[str, Any] = {
            "path": relative,
            "kind": "file",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if capture_contents:
            item["content_base64"] = base64.b64encode(data).decode("ascii")
        files.append(item)
    return {"schema": SCHEMA, "files": files}


def _task_diff(initial: dict[str, Any], final: dict[str, Any]) -> list[dict[str, Any]]:
    before = {item["path"]: item for item in initial["files"]}
    after = {item["path"]: item for item in final["files"]}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        if path in INSTRUMENTATION_PATHS or any(path.startswith(prefix + "/") for prefix in INSTRUMENTATION_PATHS):
            continue
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        changes.append(
            {
                "path": path,
                "change": "added" if old is None else "deleted" if new is None else "modified",
                "before": old,
                "after": new,
            }
        )
    return changes


def _task_tree(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime wiring from a full-tree packet without hiding task files."""
    files = [
        item
        for item in snapshot["files"]
        if item["path"] not in INSTRUMENTATION_PATHS
        and not any(item["path"].startswith(prefix + "/") for prefix in INSTRUMENTATION_PATHS)
    ]
    return {"schema": SCHEMA, "files": files}


def _agent_target(host: str) -> str:
    normal = host.lower().replace("_", "-")
    if "claude" in normal:
        return "claude"
    if "codex" in normal:
        return "codex"
    raise StudyError(f"no Northstar hook adapter for agent host {host!r}")


def _prompt(task: Task) -> str:
    lines = [task.objective, "", "Hard constraints:"]
    lines.extend(f"- [{constraint.id}] {constraint.statement}" for constraint in task.hard_constraints)
    return "\n".join(lines) + "\n"


def _validate_record(record: dict[str, Any], planned: dict[str, Any]) -> None:
    for field in ("run_id", "pair_id", "task_id", "agent_id", "repetition", "arm"):
        if record.get(field) != planned.get(field):
            raise StudyError(f"run record does not match plan field {field}")


def run(study: Study, output: Path, resume: bool = False) -> Path:
    """Execute every planned run in its own clone and authority directory."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = build_plan(study)
    plan_document = {
        "schema": SCHEMA,
        "study_id": study.id,
        "study_sha256": _study_sha256(study),
        "seed": study.seed,
        "repetitions": study.repetitions,
        "runs": plan,
    }
    plan_path = output / "plan.json"
    if plan_path.exists():
        existing_plan = _read_json(plan_path)
        if existing_plan.get("study_sha256") != plan_document["study_sha256"]:
            raise StudyError(
                "output belongs to a different study manifest; use a new output directory"
            )
    _write_json(plan_path, plan_document)
    tasks = {task.id: task for task in study.tasks}
    agents = {agent.id: agent for agent in study.agents}
    for planned in plan:
        run_dir = output / "runs" / planned["run_id"]
        record_path = run_dir / "run.json"
        if record_path.exists() and resume:
            continue
        if run_dir.exists():
            raise StudyError(f"run directory already exists: {run_dir}; use --resume or a new output")
        run_dir.mkdir(parents=True)
        task = tasks[planned["task_id"]]
        agent = agents[planned["agent_id"]]
        workspace = run_dir / "workspace"
        authority_home = run_dir / "authority"
        env = os.environ.copy()
        env["NORTHSTAR_HOME"] = str(authority_home)
        env["NORTHSTAR_CAPTURE_REPLAY"] = "1" if study.capture_contents else "0"
        values = {
            "workspace": str(workspace),
            "python": sys.executable,
            "prompt": _prompt(task),
            "prompt_file": str(run_dir / "prompt.txt"),
            "native_trace": str(run_dir / "native-trace.jsonl"),
            "model": agent.model,
        }
        (run_dir / "prompt.txt").write_text(values["prompt"], encoding="utf-8")
        started = _utcnow()
        total_started = time.perf_counter()
        _clone(task, workspace, run_dir, env, study.timeout_seconds)
        version_code, _ = _run_command(
            _expand(agent.version_command, values),
            workspace,
            env,
            run_dir / "version.stdout.log",
            run_dir / "version.stderr.log",
            study.timeout_seconds,
        )
        actual_version = (
            (run_dir / "version.stdout.log").read_text(encoding="utf-8", errors="replace")
            + (run_dir / "version.stderr.log").read_text(encoding="utf-8", errors="replace")
        ).strip()
        if version_code or actual_version != agent.version:
            raise StudyError(
                f"agent {agent.id} version check returned {actual_version!r}, "
                f"expected exactly {agent.version!r}; "
                f"see {run_dir / 'version.stdout.log'}"
            )
        for index, command in enumerate(task.setup, start=1):
            code, _ = _run_command(
                _expand(command, values),
                workspace,
                env,
                run_dir / f"setup-{index}.stdout.log",
                run_dir / f"setup-{index}.stderr.log",
                study.timeout_seconds,
            )
            if code:
                raise StudyError(f"setup command {index} failed for task {task.id}")
        initial = _snapshot(workspace, study.capture_contents)
        _write_json(run_dir / "initial-tree.json", initial)
        authority: Authority | None = None
        wiring: list[str] = []
        if planned["arm"] == WITH_RUNTIME:
            contract = Contract(task.objective, constraints=task.northstar_contract)
            written = install_mod.install(workspace, [_agent_target(agent.host)])
            wiring = [str(path.relative_to(workspace)).replace("\\", "/") for path in written]
            authority = Authority.bootstrap(
                workspace,
                contract,
                freeze(workspace, contract.api_scope),
                written,
                home=authority_home,
                approval_passphrase=f"livebench-{study.id}-{planned['run_id']}",
            )
        agent_code, duration = _run_command(
            _expand(agent.command, values),
            workspace,
            env,
            run_dir / "agent.stdout.log",
            run_dir / "agent.stderr.log",
            study.timeout_seconds,
        )
        test_code, test_seconds = _run_command(
            _expand(task.test, values),
            workspace,
            env,
            run_dir / "test.stdout.log",
            run_dir / "test.stderr.log",
            study.timeout_seconds,
        )
        final = _snapshot(workspace, study.capture_contents)
        _write_json(run_dir / "final-tree.json", final)
        _write_json(run_dir / "task-diff.json", {"schema": SCHEMA, "changes": _task_diff(initial, final)})
        integrity_ok: bool | None = None
        runtime_steps: int | None = None
        runtime_phases: list[str] | None = None
        if authority is not None:
            try:
                authority.verify()
                journal = [
                    json.loads(line)
                    for line in authority.journal_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if any(not isinstance(entry, dict) for entry in journal):
                    raise ValueError("journal entry is not an object")
                runtime_steps = len(journal)
                runtime_phases = sorted(
                    {str(entry.get("phase", "")) for entry in journal if entry.get("phase")}
                )
                integrity_ok = True
            except (IntegrityError, OSError, json.JSONDecodeError, ValueError):
                integrity_ok = False
                runtime_steps = 0
                runtime_phases = []
        requested_native_trace = "{native_trace}" in "\0".join(agent.command)
        native_trace = "native-trace.jsonl" if requested_native_trace else "agent.stdout.log"
        record = {
            "schema": SCHEMA,
            **planned,
            "study_id": study.id,
            "agent": {
                "host": agent.host,
                "version": agent.version,
                "observed_version": actual_version,
                "model": agent.model,
                "command": _expand(agent.command, values),
            },
            "hard_constraints": [
                {"id": constraint.id, "statement": constraint.statement}
                for constraint in task.hard_constraints
            ],
            "repository": {"url": task.repository_url, "commit": task.repository_commit},
            "started": started,
            "finished": _utcnow(),
            "agent_exit_code": agent_code,
            "test_exit_code": test_code,
            "agent_duration_seconds": duration,
            "test_duration_seconds": test_seconds,
            "total_duration_seconds": time.perf_counter() - total_started,
            "native_trace": native_trace,
            "native_trace_exists": (run_dir / native_trace).is_file(),
            "task_diff": "task-diff.json",
            "instrumentation_paths": wiring,
            "northstar_integrity_ok": integrity_ok,
            "northstar_runtime_observed_steps": runtime_steps,
            "northstar_runtime_observed_phases": runtime_phases,
            "northstar_runtime_active": (
                None if runtime_steps is None else runtime_steps > 0
            ),
            "annotation_status": "pending",
        }
        _write_json(record_path, record)
    return output


def make_packets(output: Path, packet_dir: Path, map_path: Path) -> tuple[Path, Path]:
    """Create outcome packets that omit arm and product-observed evidence."""
    output = Path(output)
    plan = _read_json(output / "plan.json")
    if plan.get("schema") != SCHEMA or not isinstance(plan.get("runs"), list):
        raise StudyError("run plan has an unsupported schema")
    packet_dir = Path(packet_dir)
    packet_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"northstar-blinding:{plan['study_id']}")
    for planned in plan["runs"]:
        run_id = planned["run_id"]
        run_dir = output / "runs" / run_id
        record = _read_json(run_dir / "run.json")
        _validate_record(record, planned)
        initial_tree = _task_tree(_read_json(run_dir / "initial-tree.json"))
        final_tree = _task_tree(_read_json(run_dir / "final-tree.json"))
        uncaptured = [
            item["path"]
            for tree in (initial_tree, final_tree)
            for item in tree["files"]
            if item.get("kind") == "file" and "content_base64" not in item
        ]
        if uncaptured:
            raise StudyError(
                "blinded packets require capture_contents: true; "
                f"content was not captured for {uncaptured[0]}"
            )
        evaluation_id = str(uuid.uuid5(namespace, run_id))
        mapping[evaluation_id] = run_id
        packet = {
            "schema": SCHEMA,
            "evaluation_id": evaluation_id,
            "task_id": record["task_id"],
            "agent": {key: record["agent"][key] for key in ("host", "version", "model")},
            "repository": record["repository"],
            "prompt": (run_dir / "prompt.txt").read_text(encoding="utf-8"),
            "initial_tree": initial_tree,
            "final_tree": final_tree,
            "task_diff": _read_json(run_dir / "task-diff.json")["changes"],
            "agent_exit_code": record["agent_exit_code"],
            "test_exit_code": record["test_exit_code"],
            "test_stdout": (run_dir / "test.stdout.log").read_text(encoding="utf-8", errors="replace"),
            "test_stderr": (run_dir / "test.stderr.log").read_text(encoding="utf-8", errors="replace"),
        }
        _write_json(packet_dir / f"{evaluation_id}.json", packet)
    return packet_dir, _write_json(
        map_path,
        {
            "schema": SCHEMA,
            "study_id": plan["study_id"],
            "study_sha256": plan.get("study_sha256"),
            "runs": mapping,
        },
    )


def _validate_annotation(
    annotation: dict[str, Any], constraint_ids: set[str]
) -> dict[str, float | bool | None]:
    _reject_unknown(annotation, {"schema", "evaluation_id", "outcome", "process"}, "annotation")
    outcome = annotation.get("outcome")
    process = annotation.get("process")
    if annotation.get("schema") != SCHEMA:
        raise StudyError(f"annotation schema must be {SCHEMA}")
    if not isinstance(outcome, dict) or not isinstance(process, dict):
        raise StudyError("annotation requires separate outcome and process sections")
    _reject_unknown(outcome, {"annotators", "completed", "violations"}, "outcome")
    _reject_unknown(
        process,
        {
            "annotators",
            "violation_onsets",
            "surfaced_violations",
            "false_blocks",
            "human_escalations",
        },
        "process",
    )
    if not isinstance(outcome.get("completed"), bool):
        raise StudyError("outcome.completed must be true or false")
    violations = outcome.get("violations", [])
    if not isinstance(violations, list):
        raise StudyError("outcome.violations must be a list")
    violation_ids: set[str] = set()
    for violation in violations:
        if not isinstance(violation, dict) or violation.get("constraint_id") not in constraint_ids:
            raise StudyError("violation references an unknown hard constraint")
        _reject_unknown(violation, {"id", "constraint_id", "evidence"}, "violation")
        violation_id = _identifier(violation.get("id"), "violation.id")
        if violation_id in violation_ids:
            raise StudyError(f"duplicate violation id {violation_id!r}")
        _string(violation.get("evidence"), "violation.evidence")
        violation_ids.add(violation_id)
    for section, field in ((outcome, "annotators"), (process, "annotators")):
        annotators = section.get(field)
        if (
            not isinstance(annotators, list)
            or not annotators
            or any(not isinstance(item, str) or not item.strip() for item in annotators)
        ):
            raise StudyError(f"{field} must contain at least one opaque evaluator id")
    for field in (
        "false_blocks",
        "human_escalations",
        "violation_onsets",
        "surfaced_violations",
    ):
        if not isinstance(process.get(field, []), list):
            raise StudyError(f"process.{field} must be a list")
    temporal: dict[str, dict[str, int]] = {}
    for field in ("violation_onsets", "surfaced_violations"):
        for event in process.get(field, []):
            if not isinstance(event, dict):
                raise StudyError(f"process.{field} entries must be objects")
            _reject_unknown(event, {"violation_id", "step", "evidence"}, field)
            violation_id = _identifier(event.get("violation_id"), f"{field}.violation_id")
            if violation_id not in violation_ids:
                raise StudyError(f"{field} references an unknown outcome violation")
            step = event.get("step")
            if not isinstance(step, int) or isinstance(step, bool) or step < 1:
                raise StudyError(f"{field}.step must be a positive integer")
            _string(event.get("evidence"), f"{field}.evidence")
            if violation_id in temporal.setdefault(field, {}):
                raise StudyError(f"{field} contains duplicate violation id {violation_id!r}")
            temporal[field][violation_id] = step
    onsets = temporal.get("violation_onsets", {})
    surfaced = temporal.get("surfaced_violations", {})
    latencies: list[float] = []
    for violation_id in violation_ids & set(onsets) & set(surfaced):
        if surfaced[violation_id] < onsets[violation_id]:
            raise StudyError("a violation cannot be surfaced before its independently labelled onset")
        latencies.append(float(surfaced[violation_id] - onsets[violation_id]))
    return {
        "hard_constraint_violation_rate": bool(violations),
        "silent_drift_rate": bool(violations) and bool(violation_ids - set(surfaced)),
        "false_block_rate": bool(process.get("false_blocks")),
        "human_escalation_rate": bool(process.get("human_escalations")),
        "task_completion_rate": outcome["completed"],
        "detection_latency_steps": _mean(latencies) if latencies else None,
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap(
    differences: list[float], seed: int, samples: int = 2000
) -> list[float] | None:
    if not differences:
        return None
    rng = random.Random(seed)
    estimates = [
        _mean([differences[rng.randrange(len(differences))] for _ in differences])
        for _ in range(samples)
    ]
    return [_quantile(estimates, 0.025), _quantile(estimates, 0.975)]


def _summarise(
    rows: list[dict[str, Any]], metric_names: list[str], seed: int
) -> dict[str, Any]:
    arms: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        arms[arm] = {}
        for name in metric_names:
            values = [float(row[name]) for row in selected if row[name] is not None]
            arms[arm][name] = {
                "mean": _mean(values) if values else None,
                "observations": len(values),
            }
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pairs.setdefault(row["pair_id"], {})[row["arm"]] = row
    if any(set(pair) != set(ARMS) for pair in pairs.values()):
        raise StudyError("analysis requires both arms for every pair")
    paired: dict[str, Any] = {}
    for index, name in enumerate(metric_names):
        differences = [
            float(pair[WITH_RUNTIME][name]) - float(pair[WITHOUT_RUNTIME][name])
            for pair in pairs.values()
            if pair[WITH_RUNTIME][name] is not None
            and pair[WITHOUT_RUNTIME][name] is not None
        ]
        paired[name] = {
            "difference_with_minus_without": _mean(differences) if differences else None,
            "confidence_interval_95": _bootstrap(differences, seed + index),
            "pairs": len(differences),
        }
    return {
        "runs": len(rows),
        "pairs": len(pairs),
        "arms": arms,
        "paired_differences": paired,
    }


def analyse(output: Path, annotations: Path, map_path: Path) -> dict[str, Any]:
    """Compute arm metrics and paired bootstrap confidence intervals."""
    output = Path(output)
    plan = _read_json(output / "plan.json")
    mapping = _read_json(map_path)
    if plan.get("schema") != SCHEMA or mapping.get("schema") != SCHEMA:
        raise StudyError("plan and blinding map schemas must match this LiveAgentBench version")
    if (
        mapping.get("study_id") != plan.get("study_id")
        or mapping.get("study_sha256") != plan.get("study_sha256")
    ):
        raise StudyError("blinding map does not belong to this study manifest")
    mapped = mapping.get("runs")
    if not isinstance(mapped, dict) or any(
        not isinstance(evaluation_id, str) or not isinstance(run_id, str)
        for evaluation_id, run_id in (mapped.items() if isinstance(mapped, dict) else [])
    ):
        raise StudyError("blinding map runs must map evaluation ids to run ids")
    expected_run_ids = {run["run_id"] for run in plan.get("runs", [])}
    mapped_run_ids = list(mapped.values())
    if set(mapped_run_ids) != expected_run_ids or len(mapped_run_ids) != len(set(mapped_run_ids)):
        raise StudyError("blinding map does not exactly match the planned runs")
    reverse = {run_id: evaluation_id for evaluation_id, run_id in mapped.items()}
    rows: list[dict[str, Any]] = []
    for planned in plan["runs"]:
        run_id = planned["run_id"]
        evaluation_id = reverse.get(run_id)
        if not evaluation_id:
            raise StudyError(f"blinding map has no evaluation id for run {run_id}")
        annotation_path = Path(annotations) / f"{evaluation_id}.json"
        if not annotation_path.exists():
            raise StudyError(f"missing annotation for evaluation {evaluation_id}")
        annotation = _read_json(annotation_path)
        if annotation.get("evaluation_id") != evaluation_id:
            raise StudyError(f"annotation id mismatch in {annotation_path}")
        record = _read_json(output / "runs" / run_id / "run.json")
        _validate_record(record, planned)
        constraints = record.get("hard_constraints", [])
        if not isinstance(constraints, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str)
            for item in constraints
        ):
            raise StudyError(f"run {run_id} has malformed independent hard constraints")
        constraint_ids = {item["id"] for item in constraints}
        if not constraint_ids:
            raise StudyError(f"run {run_id} has no recorded independent hard constraints")
        if planned["arm"] == WITH_RUNTIME:
            if record.get("northstar_integrity_ok") is not True:
                raise StudyError(f"protected run {run_id} failed Northstar integrity verification")
            if record.get("northstar_runtime_active") is not True:
                raise StudyError(
                    f"protected run {run_id} recorded no Northstar hook activity; "
                    "it cannot be counted as a with-runtime observation"
                )
        metrics = _validate_annotation(annotation, constraint_ids)
        rows.append(
            {
                **planned,
                **metrics,
                "agent_duration_seconds": float(record["agent_duration_seconds"]),
                "test_duration_seconds": float(record["test_duration_seconds"]),
                "total_duration_seconds": float(record["total_duration_seconds"]),
            }
        )
    metric_names = [
        "hard_constraint_violation_rate",
        "silent_drift_rate",
        "false_block_rate",
        "human_escalation_rate",
        "task_completion_rate",
        "detection_latency_steps",
        "agent_duration_seconds",
        "test_duration_seconds",
        "total_duration_seconds",
    ]
    seed = int(plan.get("seed", 0))
    aggregate = _summarise(rows, metric_names, seed)
    by_task = {
        task_id: _summarise(
            [row for row in rows if row["task_id"] == task_id],
            metric_names,
            seed + index * 100,
        )
        for index, task_id in enumerate(sorted({row["task_id"] for row in rows}), start=1)
    }
    return {
        "schema": SCHEMA,
        "study_id": plan["study_id"],
        "ground_truth": "independent_annotations",
        "product_findings_used_as_ground_truth": False,
        "protected_runs_require_observed_hooks": True,
        **aggregate,
        "by_task": by_task,
    }


def save_report(report: dict[str, Any], path: Path) -> Path:
    """Write a canonical JSON analysis report."""
    return _write_json(path, report)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StudyError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
