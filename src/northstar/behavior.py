"""The behavioural oracle: freeze what the code *does*, not just what it declares.

"Do not change the expected behaviour" is the constraint people actually care about
and the one no hash can express. The trick is not to judge it semantically at step
50 -- it is to capture it as an executable witness at step 0, while the baseline is
still the thing everyone agreed on.

So this module runs the project's own test suite once, at freeze time, and records
the outcome of every test. Later, the same suite is re-run and the outcomes are
compared. That turns a semantic question into a deterministic one:

    login() used to return True for a valid user, and the test that says so used to
    pass. It still has to.

Two properties make this honest:

* It captures the behaviour that *exists*, not the behaviour someone hoped for. A
  test failing at baseline is frozen as failing; making it pass is a change of
  behaviour like any other, and the human is told rather than congratulated.
* The witness is frozen outside the working tree and the test files are protected,
  so the agent cannot make the oracle agree with it. An agent that can edit its own
  grader has none.

ponytail: pytest and `go test` shapes only, parsed from stdout. Other runners get
UNKNOWN until someone actually needs them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"

#: Runner -> (probe that decides whether it applies, argv).
RUNNERS: tuple[tuple[str, str, list[str]], ...] = (
    ("pytest", "tests", ["pytest", "-q", "--no-header", "-rA", "--timeout=120"]),
    ("pytest", "test", ["pytest", "-q", "--no-header", "-rA", "--timeout=120"]),
    ("go", "go.mod", ["go", "test", "./...", "-v"]),
)

_PYTEST_LINE = re.compile(r"^(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)\s+(?P<name>\S+)", re.M)
_GO_LINE = re.compile(r"^\s*---\s+(?P<outcome>PASS|FAIL|SKIP):\s+(?P<name>\S+)", re.M)

_OUTCOMES = {
    "PASSED": PASSED,
    "PASS": PASSED,
    "FAILED": FAILED,
    "FAIL": FAILED,
    "ERROR": FAILED,
    "SKIPPED": SKIPPED,
    "SKIP": SKIPPED,
}


@dataclass
class Run:
    """Result of one behavioural capture."""

    outcomes: dict[str, str]
    command: list[str]
    #: Set when the suite could not be run at all -- reported as UNKNOWN, never as
    #: "no behaviour changed".
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and bool(self.outcomes)


def parse_pytest(output: str) -> dict[str, str]:
    return {m.group("name"): _OUTCOMES[m.group("outcome")] for m in _PYTEST_LINE.finditer(output)}


def parse_go(output: str) -> dict[str, str]:
    return {m.group("name"): _OUTCOMES[m.group("outcome")] for m in _GO_LINE.finditer(output)}


def detect(root: Path) -> list[str] | None:
    """Pick a test command for this project, or None if there is nothing to run."""
    root = Path(root)
    for _, probe, argv in RUNNERS:
        if (root / probe).exists() and shutil.which(argv[0]):
            return argv
    return None


def capture(root: Path, command: list[str] | None = None, timeout: int = 600) -> Run:
    """Run the suite and record per-test outcomes.

    Failures to *run* are distinguished from failing tests. A suite that cannot
    execute yields an error, not an empty -- and therefore vacuously satisfied --
    set of outcomes.
    """
    argv = command or detect(root)
    if not argv:
        return Run({}, [], error="no supported test runner found")
    try:
        completed = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return Run({}, argv, error=f"{argv[0]} is not installed")
    except subprocess.TimeoutExpired:
        return Run({}, argv, error=f"test suite exceeded {timeout}s")
    except OSError as exc:  # pragma: no cover - platform specific
        return Run({}, argv, error=str(exc))

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    parser = parse_go if argv[0] == "go" else parse_pytest
    outcomes = parser(output)
    if not outcomes:
        return Run({}, argv, error="test output could not be parsed")
    return Run(outcomes, argv)


def compare(baseline: dict[str, str], current: dict[str, str]) -> list[tuple[str, str, str]]:
    """(test, before, after) for every test whose outcome moved.

    A test that starts passing is reported too. "It passes now" is a behaviour
    change; whether it is a welcome one is the human's call, not the runtime's.
    """
    changed: list[tuple[str, str, str]] = []
    for name, before in sorted(baseline.items()):
        after = current.get(name, "missing")
        if after != before:
            changed.append((name, before, after))
    for name, after in sorted(current.items()):
        if name not in baseline:
            changed.append((name, "absent", after))
    return changed
