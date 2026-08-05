from __future__ import annotations

import pytest

from northstar import surface
from northstar.surface import EXACT, HEURISTIC, extract, has_surface

# ------------------------------------------------------------------- python


def test_python_is_exact():
    result = extract("def f(a: int = 1) -> str:\n    pass\n", "m.py")
    assert result.fidelity == EXACT
    assert result.is_exact
    assert result.symbols == {"m.py::f": "(a: int=1) -> str"}


def test_python_syntax_error_propagates():
    with pytest.raises(SyntaxError):
        extract("def (:\n", "m.py")


# --------------------------------------------------------------- javascript


@pytest.mark.parametrize(
    "source,symbol,signature",
    [
        ("export function login(user, pass) {}", "a.js::login", "(user, pass)"),
        ("export async function fetchAll(url) {}", "a.js::fetchAll", "(url)"),
        ("export class Session {}", "a.js::Session", "class"),
        ("export class Admin extends User {}", "a.js::Admin", "extends User"),
        ("export const helper = (a, b) => a + b;", "a.js::helper", "(a, b)"),
        ("export const VERSION = '1';", "a.js::VERSION", "value"),
        ("module.exports.connect = function (opts) {};", "a.js::connect", "(opts)"),
    ],
)
def test_javascript_exports(source, symbol, signature):
    result = extract(source, "a.js")
    assert result.fidelity == HEURISTIC
    assert not result.is_exact
    assert result.symbols[symbol] == signature


def test_typescript_types_and_interfaces():
    result = extract("export interface User { id: string }\nexport type Id = string\n", "a.ts")
    assert "a.ts::User" in result.symbols
    assert "a.ts::Id" in result.symbols


def test_javascript_default_export():
    assert "a.js::default" in extract("export default function main() {}", "a.js").symbols


def test_javascript_ignores_private_helpers():
    assert extract("function internal() {}\nconst x = 1;\n", "a.js").symbols == {}


# --------------------------------------------------------------------- go


def test_go_exports_by_capitalisation():
    source = (
        "package auth\n\n"
        "func Login(user string, pw string) bool { return true }\n"
        "func internal() {}\n"
        "type Session struct{}\n"
        "func (s *Session) Refresh(force bool) error { return nil }\n"
    )
    symbols = extract(source, "auth.go").symbols
    assert symbols["auth.go::Login"] == "(user string, pw string) bool"
    assert symbols["auth.go::Session"] == "type struct"
    assert symbols["auth.go::Session.Refresh"] == "(force bool) error"
    assert "auth.go::internal" not in symbols


# ------------------------------------------------------------------- rust


def test_rust_pub_items():
    source = "pub fn login(user: &str) -> bool { true }\nfn hidden() {}\npub struct Session;\n"
    symbols = extract(source, "lib.rs").symbols
    assert symbols["lib.rs::login"] == "fn (user: &str) -> bool"
    assert symbols["lib.rs::Session"] == "struct"
    assert "lib.rs::hidden" not in symbols


def test_rust_scoped_pub():
    assert "lib.rs::helper" in extract("pub(crate) fn helper() {}", "lib.rs").symbols


# ------------------------------------------------------------------- java


def test_java_public_members():
    source = (
        "public class Session {\n"
        "    public boolean refresh(int tries) { return true; }\n"
        "    private void hidden() {}\n"
        "}\n"
    )
    symbols = extract(source, "Session.java").symbols
    assert symbols["Session.java::Session"] == "class"
    assert symbols["Session.java::refresh"] == "(int tries) -> boolean"
    assert "Session.java::hidden" not in symbols


# ---------------------------------------------------------------- registry


def test_unsupported_language_returns_none_not_an_empty_surface():
    """An empty surface is always satisfied. UNKNOWN is the honest answer."""
    assert extract("SECTION foo", "a.cobol") is None
    assert extract("x", "noextension") is None


def test_has_surface():
    assert has_surface("a.py") and has_surface("a.go")
    assert not has_surface("a.md") and not has_surface("a")


def test_files_without_an_api_are_not_gaps():
    assert "a.md".endswith(surface.NO_SURFACE)
    assert "pyproject.toml".endswith(surface.NO_SURFACE)
    assert not "a.py".endswith(surface.NO_SURFACE)
