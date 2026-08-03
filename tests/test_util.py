from __future__ import annotations

import subprocess
from pathlib import Path

from northstar import util


def test_normalize_strips_prefixes_and_backslashes():
    assert util.normalize("./src\\auth/service.py") == "src/auth/service.py"
    assert util.normalize("/tests/a.py") == "tests/a.py"
    assert util.normalize(Path("a/b.py")) == "a/b.py"


def test_matches_any_glob_and_recursive_prefix():
    assert util.matches_any("tests/unit/test_a.py", ["tests/**"]) == "tests/**"
    assert util.matches_any("tests", ["tests/**"]) == "tests/**"
    assert util.matches_any("src/a.py", ["**/*.py"]) == "**/*.py"
    assert util.matches_any("src/a.py", ["tests/**"]) is None
    assert util.matches_any("src/a.py", []) is None


def test_matches_any_does_not_match_sibling_prefix():
    # `tests/**` must not swallow `tests_helpers/`
    assert util.matches_any("tests_helpers/x.py", ["tests/**"]) is None


def test_iter_source_files_skips_ignored_dirs(project: Path):
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (project / ".northstar").mkdir()
    (project / ".northstar" / "oracle.json").write_text("{}", encoding="utf-8")
    (project / "image.png").write_bytes(b"\x89PNG")

    found = {p.as_posix() for p in util.iter_source_files(project)}
    assert "src/auth/service.py" in found
    assert not any(f.startswith("node_modules") for f in found)
    assert not any(f.startswith(".northstar") for f in found)
    assert "image.png" not in found


def test_hash_and_lines(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_text("one\ntwo\n", encoding="utf-8")
    assert util.hash_file(path) == util.hash_text("one\ntwo\n")
    assert util.hash_text("a") != util.hash_text("b")
    assert util.count_lines(util.read_text(path)) == 2


def test_git_commit_returns_none_outside_repo(tmp_path: Path):
    assert util.git_commit(tmp_path) is None


def test_git_commit_reads_head(tmp_path: Path):
    def run(*args: str) -> None:
        subprocess.run(args, cwd=str(tmp_path), capture_output=True, check=False)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")

    commit = util.git_commit(tmp_path)
    if commit is not None:  # git may be unavailable in a bare CI image
        assert len(commit) == 40


def test_find_root_walks_up(tmp_path: Path):
    (tmp_path / util.STATE_DIR).mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert util.find_root(nested) == tmp_path.resolve()


def test_find_root_falls_back_to_start(tmp_path: Path):
    assert util.find_root(tmp_path) == tmp_path.resolve()


def test_state_dir(tmp_path: Path):
    assert util.state_dir(tmp_path).name == util.STATE_DIR
