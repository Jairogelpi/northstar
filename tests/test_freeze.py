from __future__ import annotations

import json
from pathlib import Path

import pytest

import northstar.freeze as fz
from northstar.freeze import Oracle, freeze, oracle_path

from .conftest import write

# ------------------------------------------------------------- api extraction


def test_extract_api_captures_public_surface_only():
    source = (
        "def public(a, b=1):\n    pass\n"
        "def _private():\n    pass\n"
        "class Thing:\n"
        "    def __init__(self, x):\n        pass\n"
        "    def method(self):\n        pass\n"
        "    def _hidden(self):\n        pass\n"
        "class _Internal:\n    pass\n"
    )
    api = fz.extract_api(source, "m.py")
    assert set(api) == {"m.py::public", "m.py::Thing", "m.py::Thing.__init__", "m.py::Thing.method"}


def test_signature_records_every_observable_part():
    source = (
        "def f(a, /, b: int, c: str = 'x', *args, d: bool = False, **kw) -> dict:\n"
        "    pass\n"
    )
    sig = fz.extract_api(source, "m.py")["m.py::f"]
    assert sig == "(a, /, b: int, c: str='x', *args, d: bool=False, **kw) -> dict"


def test_keyword_only_marker_without_vararg():
    sig = fz.extract_api("def f(a, *, b):\n    pass\n", "m.py")["m.py::f"]
    assert sig == "(a, *, b)"


def test_async_function_is_public_api():
    api = fz.extract_api("async def fetch(url: str) -> bytes:\n    pass\n", "m.py")
    assert api["m.py::fetch"] == "(url: str) -> bytes"


def test_class_bases_are_part_of_the_signature():
    api = fz.extract_api("class A(Base, Mixin):\n    pass\n", "m.py")
    assert api["m.py::A"] == "class(Base, Mixin)"


def test_dunder_all_defines_the_surface():
    source = "__all__ = ['kept']\ndef kept():\n    pass\ndef alsopublic():\n    pass\n"
    assert set(fz.extract_api(source, "m.py")) == {"m.py::kept"}


def test_syntax_error_propagates_so_caller_can_mark_unknown():
    with pytest.raises(SyntaxError):
        fz.extract_api("def (:\n", "m.py")


# ------------------------------------------------------------- dependencies


def test_dependencies_from_all_manifests(tmp_path: Path):
    write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["Requests>=2", "flask[async]"]\n')
    write(tmp_path, "requirements.txt", "# comment\nnumpy==1.0\n-r other.txt\n\npandas\n")
    write(tmp_path, "package.json", '{"dependencies": {"React": "^18"}, "devDependencies": {"jest": "1"}}')

    deps = fz.extract_dependencies(tmp_path)
    assert deps["pyproject.toml"] == ["flask", "requests"]
    assert deps["requirements.txt"] == ["numpy", "pandas"]
    assert deps["package.json"] == ["react"]  # dev deps excluded


def test_malformed_manifests_yield_no_deps_instead_of_crashing(tmp_path: Path):
    write(tmp_path, "pyproject.toml", "this is not toml {{{")
    write(tmp_path, "package.json", "{not json")
    deps = fz.extract_dependencies(tmp_path)
    assert deps == {"pyproject.toml": [], "package.json": []}


def test_no_manifests(tmp_path: Path):
    assert fz.extract_dependencies(tmp_path) == {}


# ------------------------------------------------------------- module graph


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/auth/service.py", "auth.service"),
        ("lib/a.py", "a"),
        ("src/auth/__init__.py", "auth"),
        ("top.py", "top"),
    ],
)
def test_module_name(path, expected):
    assert fz.module_name(path) == expected


def test_extract_imports_ignores_relative():
    source = "import os\nfrom a.b import c\nfrom . import sibling\nfrom .x import y\n"
    assert fz.extract_imports(source) == {"os", "a.b"}


def test_resolve_prefers_longest_known_module():
    known = {"a", "a.b"}
    assert fz._resolve("a.b.c", known) == "a.b"
    assert fz._resolve("a.z", known) == "a"
    assert fz._resolve("external.lib", known) is None


# -------------------------------------------------------------------- oracle


def test_freeze_captures_the_repo(project: Path):
    oracle = freeze(project, ["**/*.py"])

    assert "src/auth/service.py" in oracle.files
    assert oracle.files["src/auth/service.py"]["lines"] > 0
    assert oracle.api["src/auth/service.py::login"] == "(user: str, password: str) -> bool"
    assert "src/auth/service.py::Session.refresh" in oracle.api
    assert "src/auth/service.py::_hash" not in oracle.api
    assert oracle.dependencies["pyproject.toml"] == ["pyyaml", "requests"]
    assert oracle.module_graph["auth.service"] == ["db"]
    assert oracle.unknown == []


def test_api_scope_limits_what_is_frozen(project: Path):
    oracle = freeze(project, ["src/db.py"])
    assert set(oracle.api) == {"src/db.py::connect"}
    # out-of-scope files are still hashed and still contribute graph edges
    assert "src/auth/service.py" in oracle.files
    assert "auth.service" in oracle.module_graph


def test_unparseable_file_is_unknown_not_silently_clean(project: Path):
    write(project, "src/broken.py", "def (:\n")
    oracle = freeze(project, ["**/*.py"])
    assert oracle.unknown == ["src/broken.py"]


def test_freeze_defaults_to_all_python(project: Path):
    assert "src/db.py::connect" in freeze(project).api


def test_oracle_roundtrip(project: Path):
    oracle = freeze(project, ["**/*.py"])
    path = oracle.save(project)
    assert path == oracle_path(project)

    loaded = Oracle.load(project)
    assert loaded.to_dict() == oracle.to_dict()
    assert json.loads(path.read_text(encoding="utf-8"))["api"] == oracle.api


def test_oracle_load_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="no oracle"):
        Oracle.load(tmp_path)


def test_freeze_is_deterministic(project: Path):
    assert freeze(project, ["**/*.py"]).to_dict()["files"] == freeze(project, ["**/*.py"]).to_dict()["files"]
