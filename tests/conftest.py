from __future__ import annotations

from pathlib import Path

import pytest

from northstar.authority import Authority
from northstar.contract import Contract, default_contract
from northstar.freeze import Oracle, freeze

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2.0", "pyyaml"]
"""

SERVICE = '''\
"""Auth service."""
from db import connect


def login(user: str, password: str) -> bool:
    """Public API."""
    return bool(connect() and user and password)


def _hash(value):
    return value


class Session:
    def __init__(self, user: str):
        self.user = user

    def refresh(self, *, force: bool = False) -> None:
        pass

    def _secret(self):
        pass
'''

DB = """\
def connect():
    return True
"""

TEST_AUTH = """\
from auth.service import login


def test_login():
    assert login("a", "b")
"""

APPROVAL_SECRET = "northstar-test-secret"


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Trusted state must never leak between tests or into the developer profile."""
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar-authority"))
    monkeypatch.setattr(
        "northstar.cli.prompt_new_approval_secret",
        lambda: APPROVAL_SECRET,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small but realistic repo: package under src/, tests, a manifest."""
    write(tmp_path, "pyproject.toml", PYPROJECT)
    write(tmp_path, "src/auth/__init__.py", "")
    write(tmp_path, "src/auth/service.py", SERVICE)
    write(tmp_path, "src/db.py", DB)
    write(tmp_path, "tests/test_auth.py", TEST_AUTH)
    return tmp_path


@pytest.fixture
def contract() -> Contract:
    return default_contract("refactor authentication")


@pytest.fixture
def governed(project: Path, contract: Contract) -> tuple[Path, Contract, Oracle]:
    """Project with a saved contract and a frozen baseline."""
    oracle = freeze(project, contract.api_scope)
    Authority.bootstrap(project, contract, oracle, approval_passphrase=APPROVAL_SECRET)
    return project, contract, oracle
