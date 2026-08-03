"""Public API extraction, per language.

Python is parsed with the real AST, so its surface is exact. Every other language
is matched with patterns, which is a genuine ceiling: a pattern extractor sees the
declarations people actually write and misses the clever ones.

That ceiling is declared rather than hidden. Each extractor reports its own
`fidelity`, findings derived from a heuristic extractor say so in their detail, and
a file whose language has no extractor at all becomes UNKNOWN instead of silently
contributing an empty -- and therefore always-satisfied -- surface.

ponytail: patterns, not per-language parsers. A tree-sitter grammar per language is
the upgrade path, and worth it the moment a real project reports a miss.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable

EXACT = "ast"
HEURISTIC = "heuristic"


@dataclass
class Surface:
    symbols: dict[str, str]
    fidelity: str

    @property
    def is_exact(self) -> bool:
        return self.fidelity == EXACT


# ------------------------------------------------------------------- python


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    padding = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if index >= padding:
            text += f"={ast.unparse(args.defaults[index - padding])}"
        if args.posonlyargs and arg is args.posonlyargs[-1]:
            text += ", /"
        parts.append(text)
    if args.vararg is not None:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f"={ast.unparse(default)}"
        parts.append(text)
    if args.kwarg is not None:
        parts.append(f"**{args.kwarg.arg}")
    rendered = f"({', '.join(parts)})"
    if node.returns is not None:
        rendered += f" -> {ast.unparse(node.returns)}"
    return rendered


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def python_surface(source: str, relpath: str) -> Surface:
    """Exact: public module-level defs/classes and public methods of public classes.

    Raises SyntaxError so the caller records UNKNOWN rather than an empty surface.
    """
    tree = ast.parse(source)
    symbols: dict[str, str] = {}
    declared: set[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            try:
                declared = {str(v) for v in ast.literal_eval(node.value)}
            except (ValueError, SyntaxError):  # pragma: no cover - exotic __all__
                declared = None

    def exported(name: str) -> bool:
        return name in declared if declared is not None else _is_public(name)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and exported(node.name):
            symbols[f"{relpath}::{node.name}"] = _signature(node)
        elif isinstance(node, ast.ClassDef) and exported(node.name):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            symbols[f"{relpath}::{node.name}"] = f"class({bases})"
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    _is_public(child.name) or child.name == "__init__"
                ):
                    symbols[f"{relpath}::{node.name}.{child.name}"] = _signature(child)
    return Surface(symbols, EXACT)


# --------------------------------------------------------------- javascript


#: (pattern, label used when the declaration carries no signature of its own).
_JS_PATTERNS = (
    (re.compile(r"^\s*export\s+(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*(?P<sig>\([^)]*\))", re.M), "function"),
    (re.compile(r"^\s*export\s+class\s+(?P<name>\w+)(?P<sig>[^{]*)\{", re.M), "class"),
    (
        re.compile(
            r"^\s*export\s+(?:const|let|var)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*"
            r"(?:async\s*)?(?P<sig>\([^)]*\))?",
            re.M,
        ),
        "value",
    ),
    (re.compile(r"^\s*export\s+(?:type|interface)\s+(?P<name>\w+)(?P<sig>[^={]*)", re.M), "type"),
    (re.compile(r"^\s*module\.exports\.(?P<name>\w+)\s*=\s*(?:function\s*)?(?P<sig>\([^)]*\))?", re.M), "value"),
)


def javascript_surface(source: str, relpath: str) -> Surface:
    symbols: dict[str, str] = {}
    for pattern, label in _JS_PATTERNS:
        for match in pattern.finditer(source):
            name = match.group("name")
            signature = (match.group("sig") or "").strip() or label
            symbols.setdefault(f"{relpath}::{name}", " ".join(signature.split()))
    for match in re.finditer(r"^\s*export\s+default\s+(?:async\s+)?(?:function\s+)?(\w+)?", source, re.M):
        symbols.setdefault(f"{relpath}::default", match.group(1) or "default")
    return Surface(symbols, HEURISTIC)


# --------------------------------------------------------------------- go


_GO_FUNC = re.compile(r"^\s*func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>[A-Z]\w*)\s*(?P<sig>\([^{]*)", re.M)
_GO_TYPE = re.compile(r"^\s*type\s+(?P<name>[A-Z]\w*)\s+(?P<sig>\w+)", re.M)


def go_surface(source: str, relpath: str) -> Surface:
    """Go exports by capitalisation, which makes the public set unusually crisp."""
    symbols: dict[str, str] = {}
    for match in _GO_FUNC.finditer(source):
        receiver = match.group("recv")
        owner = ""
        if receiver:
            parts = receiver.replace("*", "").split()
            owner = f"{parts[-1]}." if parts else ""
        signature = " ".join(match.group("sig").split()).rstrip("{").strip()
        symbols[f"{relpath}::{owner}{match.group('name')}"] = signature
    for match in _GO_TYPE.finditer(source):
        symbols[f"{relpath}::{match.group('name')}"] = f"type {match.group('sig')}"
    return Surface(symbols, HEURISTIC)


# ------------------------------------------------------------------- rust


_RUST = re.compile(
    r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?P<kind>fn|struct|enum|trait|const|type)\s+(?P<name>\w+)"
    r"(?P<sig>[^{;]*)",
    re.M,
)


def rust_surface(source: str, relpath: str) -> Surface:
    symbols = {
        f"{relpath}::{m.group('name')}": f"{m.group('kind')} {' '.join(m.group('sig').split())}".strip()
        for m in _RUST.finditer(source)
    }
    return Surface(symbols, HEURISTIC)


# ------------------------------------------------------------------- java


_JAVA_TYPE = re.compile(r"^\s*public\s+(?:final\s+|abstract\s+)?(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)", re.M)
_JAVA_METHOD = re.compile(
    r"^\s*public\s+(?:static\s+|final\s+|synchronized\s+)*(?P<type>[\w<>\[\],\s.]+?)\s+"
    r"(?P<name>\w+)\s*(?P<sig>\([^)]*\))",
    re.M,
)


def java_surface(source: str, relpath: str) -> Surface:
    symbols: dict[str, str] = {}
    for match in _JAVA_TYPE.finditer(source):
        symbols[f"{relpath}::{match.group('name')}"] = match.group("kind")
    for match in _JAVA_METHOD.finditer(source):
        if match.group("type").strip() in ("class", "interface", "enum", "record"):
            continue
        signature = " ".join(match.group("sig").split())
        symbols[f"{relpath}::{match.group('name')}"] = f"{signature} -> {match.group('type').strip()}"
    return Surface(symbols, HEURISTIC)


# ------------------------------------------------------------------ registry


EXTRACTORS: dict[str, Callable[[str, str], Surface]] = {
    ".py": python_surface,
    ".pyi": python_surface,
    ".js": javascript_surface,
    ".jsx": javascript_surface,
    ".ts": javascript_surface,
    ".tsx": javascript_surface,
    ".mjs": javascript_surface,
    ".go": go_surface,
    ".rs": rust_surface,
    ".java": java_surface,
}

#: Files we deliberately do not read for API surface. Not a gap -- they have none.
NO_SURFACE = (".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sql")


def extract(source: str, relpath: str) -> Surface | None:
    """Surface for a file, or None when the language has no extractor.

    None means UNKNOWN at the call site. An unparsed file is not a safe file.
    """
    suffix = "." + relpath.rsplit(".", 1)[-1] if "." in relpath else ""
    extractor = EXTRACTORS.get(suffix)
    if extractor is None:
        return None
    return extractor(source, relpath)


def has_surface(relpath: str) -> bool:
    suffix = "." + relpath.rsplit(".", 1)[-1] if "." in relpath else ""
    return suffix in EXTRACTORS
