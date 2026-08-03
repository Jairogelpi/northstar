"""Intent compiler: natural language -> contract constraints.

Deliberately rule-based, not model-based. A model translating prose into YAML can
mistranslate, and a wrong contract blocks for the wrong reason -- which costs more
trust than it buys, because the whole value of the deterministic layer is that its
refusals are never arguable.

So this compiler does three things instead:

1. Matches only phrasings it actually recognises.
2. Records the source phrase next to every constraint it emits, so the human can
   audit the translation at a glance.
3. Reports everything it did not understand as `unmatched`, rather than guessing.

Its accuracy is a measured number, not an assumption -- see
`tests/test_compiler.py`, which scores it against a fixture corpus and fails the
build if precision or recall regress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .contract import APPROVAL_REQUIRED, FORBIDDEN, Contract

#: A phrase that scopes a rule to "ask first" rather than "never".
_ASK = re.compile(r"\b(ask|check|confirm|consult|pregunta|consulta)\b[^.]*\b(before|first|antes)\b", re.I)

_NEGATION = re.compile(
    r"\b(do not|don't|dont|never|no|without|avoid|refrain from|forbid|"
    r"nunca|sin|evita|no)\b",
    re.I,
)

_PATH_TOKEN = re.compile(r"[`'\"]([\w./*-]+/[\w./*-]*|\*\*/[\w.*-]+|[\w-]+/\*\*)[`'\"]")


@dataclass
class Rule:
    """One recognised phrasing and the constraint it compiles to."""

    name: str
    pattern: re.Pattern[str]
    section: str
    key: str
    #: Value when the sentence is a prohibition, and when it is an "ask first".
    forbid: Any = FORBIDDEN
    approve: Any = APPROVAL_REQUIRED
    #: True when the rule appends to a list instead of setting a scalar.
    appends: bool = False
    needs_negation: bool = True


RULES: tuple[Rule, ...] = (
    Rule(
        "public_api",
        re.compile(
            r"\b(public|external|exported)?\s*api\b|\bbreaking change|"
            r"\bbackwards?[- ]compat|\bretrocompat|\bapi p[úu]blica",
            re.I,
        ),
        "public_api",
        "change",
    ),
    Rule(
        "dependencies",
        re.compile(
            r"\b(add|introduce|new|extra|a[ñn]ad|nueva)\w*\s+"
            r"(\w+\s+){0,2}(dependenc|librar|package|paquete|librer)",
            re.I,
        ),
        "dependencies",
        "additions",
    ),
    Rule(
        "protected_tests",
        re.compile(r"\b(existing\s+)?(unit\s+|integration\s+)?tests?\b|\bpruebas\b|\btests\b", re.I),
        "protected_paths",
        "tests/**",
        appends=True,
    ),
    Rule(
        "database_schema",
        re.compile(
            r"\b(database|db|schema|migration|esquema|migraci[óo]n)\w*\b",
            re.I,
        ),
        "protected_paths",
        "migrations/**",
        appends=True,
    ),
    Rule(
        "ci_config",
        re.compile(r"\b(ci|continuous integration|workflow|pipeline|github action)\w*\b", re.I),
        "protected_paths",
        ".github/**",
        appends=True,
    ),
    Rule(
        "module_graph",
        re.compile(
            r"\b(architecture|coupling|module (structure|boundar|dependenc)|"
            r"arquitectura|acoplamiento)\w*\b",
            re.I,
        ),
        "module_graph",
        "new_edges",
    ),
    Rule(
        "push",
        re.compile(r"\b(push|deploy|publish|release|despliegue|publiqu)\w*\b", re.I),
        "commands",
        "git push*",
        appends=True,
    ),
)

#: Phrasings we recognise as a constraint but cannot enforce. Reported, never
#: silently dropped -- an unenforceable promise is worse than an admitted gap.
UNENFORCEABLE = (
    (re.compile(r"\bpython\s*3?\.?\d+", re.I), "python version support is not a checkable invariant yet"),
    (re.compile(r"\b(simple|clean|readable|idiomatic|elegant|simpl|limpi)\w*\b", re.I),
     "subjective quality bars cannot be frozen deterministically"),
    (re.compile(r"\b(performance|faster|latency|rendimiento|velocidad)\w*\b", re.I),
     "performance goals need a benchmark oracle, not a static check"),
)

_SCOPE_FILES = re.compile(r"\b(at most|no more than|m[áa]ximo|hasta)\s+(\d+)\s+(files?|ficheros?|archivos?)", re.I)
_SCOPE_LINES = re.compile(r"\b(at most|no more than|m[áa]ximo|hasta)\s+(\d+)\s+(lines?|l[íi]neas?)", re.I)


@dataclass
class Compiled:
    """The compiler's full output, including what it could not translate."""

    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    #: constraint path -> the sentence that produced it.
    provenance: dict[str, str] = field(default_factory=dict)
    #: Sentences that look like constraints but were not translated.
    unmatched: list[str] = field(default_factory=list)
    #: Sentences understood but not enforceable, with the reason.
    unenforceable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of constraint-like sentences that produced a real constraint."""
        total = len(self.provenance) + len(self.unmatched) + len(self.unenforceable)
        return len(self.provenance) / total if total else 1.0

    def to_contract(self) -> Contract:
        return Contract(objective=self.objective, constraints=self.constraints)


def split_sentences(text: str) -> list[str]:
    """Split on line breaks, bullets and sentence enders.

    Instructions arrive as bullet lists at least as often as prose, and a bullet
    list collapsed into one sentence is the easiest way to lose a constraint.
    """
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line:
            continue
        parts.extend(p.strip() for p in re.split(r"(?<=[.;!?])\s+", line) if p.strip())
    return parts


def _looks_like_a_constraint(sentence: str) -> bool:
    return bool(
        _NEGATION.search(sentence)
        or _ASK.search(sentence)
        or _SCOPE_FILES.search(sentence)
        or _SCOPE_LINES.search(sentence)
        or re.search(r"\b(must|should|keep|preserve|maintain|only|mant[ée]n|conserva|debe)\b", sentence, re.I)
    )


def _set(constraints: dict[str, Any], section: str, key: str, value: Any, appends: bool) -> str:
    if section == "protected_paths":
        bucket = constraints.setdefault(section, [])
        if key not in bucket:
            bucket.append(key)
        return f"protected_paths[{key}]"

    bucket = constraints.setdefault(section, {})
    if appends:  # `commands` keeps a list of globs under `forbidden`
        target = bucket.setdefault("forbidden", [])
        if key not in target:
            target.append(key)
        return f"{section}.forbidden[{key}]"
    bucket[key] = value
    return f"{section}.{key}"


def compile_intent(text: str, objective: str | None = None) -> Compiled:
    """Translate a task description into contract constraints.

    Only prohibitions and "ask first" phrasings become constraints. A sentence that
    merely describes the goal is not a constraint, and inventing one from it is how
    a compiler starts blocking work nobody asked it to block.
    """
    sentences = split_sentences(text)
    # The objective is the first sentence that is *not* itself a constraint. A task
    # given as a single prohibition has no objective line to spare, and swallowing
    # it as one would silently drop the only constraint present.
    inferred = next((s for s in sentences if not _looks_like_a_constraint(s)), "")
    result = Compiled(objective=(objective or inferred).strip())

    for sentence in sentences:
        if objective is None and sentence == inferred:
            continue
        if not _looks_like_a_constraint(sentence):
            continue

        asks = bool(_ASK.search(sentence))
        negated = bool(_NEGATION.search(sentence))
        matched = False

        for scope_pattern, key in ((_SCOPE_FILES, "max_files"), (_SCOPE_LINES, "max_lines")):
            found = scope_pattern.search(sentence)
            if found:
                path = _set(result.constraints, "scope", key, int(found.group(2)), False)
                result.provenance[path] = sentence
                matched = True

        for rule in RULES:
            if not rule.pattern.search(sentence):
                continue
            if rule.needs_negation and not (negated or asks):
                continue
            value = rule.approve if asks else rule.forbid
            path = _set(result.constraints, rule.section, rule.key, value, rule.appends)
            result.provenance[path] = sentence
            matched = True

        for explicit in _PATH_TOKEN.findall(sentence):
            if negated or asks:
                path = _set(result.constraints, "protected_paths", explicit, None, True)
                result.provenance[path] = sentence
                matched = True

        if matched:
            continue

        reason = next((why for pattern, why in UNENFORCEABLE if pattern.search(sentence)), None)
        if reason is not None:
            result.unenforceable.append((sentence, reason))
        else:
            result.unmatched.append(sentence)

    return result


def render(compiled: Compiled) -> str:
    """Annotated YAML: every constraint sits next to the phrase that produced it."""
    lines = [
        "# Compiled by `northstar compile` from your task description.",
        "# Every constraint records the sentence it came from. Read them: an intent",
        "# compiler that is trusted without review is just a slower way to guess.",
        f"objective: {compiled.objective}",
        "",
        "constraints:",
    ]

    def note(path: str) -> str:
        source = compiled.provenance.get(path)
        return f"  # from: \"{source}\"" if source else ""

    protected = compiled.constraints.get("protected_paths", [])
    if protected:
        lines.append("  protected_paths:")
        for entry in protected:
            lines.append(f"    - {entry}{note(f'protected_paths[{entry}]')}")
        lines.append("")

    for section in ("public_api", "dependencies", "module_graph"):
        values = compiled.constraints.get(section)
        if not values:
            continue
        lines.append(f"  {section}:")
        for key, value in values.items():
            lines.append(f"    {key}: {value}{note(f'{section}.{key}')}")
        lines.append("")

    scope = compiled.constraints.get("scope")
    if scope:
        lines.append("  scope:")
        for key, value in scope.items():
            lines.append(f"    {key}: {value}{note(f'scope.{key}')}")
        lines.append("")

    commands = (compiled.constraints.get("commands") or {}).get("forbidden") or []
    if commands:
        lines.append("  commands:")
        lines.append("    forbidden:")
        for entry in commands:
            lines.append(f"      - \"{entry}\"{note(f'commands.forbidden[{entry}]')}")
        lines.append("")

    if compiled.unenforceable or compiled.unmatched:
        lines.append("# NOT COMPILED -- these are on you, not on the runtime:")
        for sentence, reason in compiled.unenforceable:
            lines.append(f'#   "{sentence}"')
            lines.append(f"#       {reason}")
        for sentence in compiled.unmatched:
            lines.append(f'#   "{sentence}"')
            lines.append("#       not recognised; add it to the contract by hand if it matters")
    return "\n".join(lines).rstrip() + "\n"


def score(cases: Iterable[tuple[str, set[str]]]) -> dict[str, float]:
    """Precision/recall of the compiler over labelled cases.

    Used by the test suite to hold the compiler to a measured number instead of a
    claim. `cases` are (text, expected constraint paths).
    """
    true_positive = false_positive = false_negative = 0
    for text, expected in cases:
        produced = set(compile_intent(text).provenance)
        true_positive += len(produced & expected)
        false_positive += len(produced - expected)
        false_negative += len(expected - produced)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}
