"""The compiler is held to a measured number, not a claim."""

from __future__ import annotations

import pytest

from northstar.compiler import compile_intent, render, score, split_sentences
from northstar.contract import APPROVAL_REQUIRED, FORBIDDEN

TASK = """\
Refactor authentication.

Do not change the public API.
Do not add runtime dependencies.
Preserve Python 3.11 support.
Do not modify the existing tests.
Ask before changing the database schema.
"""


def test_the_worked_example_compiles():
    compiled = compile_intent(TASK)

    assert compiled.objective == "Refactor authentication."
    assert compiled.constraints["public_api"]["change"] == FORBIDDEN
    assert compiled.constraints["dependencies"]["additions"] == FORBIDDEN
    assert "tests/**" in compiled.constraints["protected_paths"]
    assert "migrations/**" in compiled.constraints["protected_paths"]
    assert compiled.to_contract().version == 1


def test_ask_before_becomes_approval_not_denial():
    compiled = compile_intent("Ask before changing the architecture.")
    assert compiled.constraints["module_graph"]["new_edges"] == APPROVAL_REQUIRED


def test_prohibition_becomes_denial():
    compiled = compile_intent("Never change the architecture.")
    assert compiled.constraints["module_graph"]["new_edges"] == FORBIDDEN


def test_every_constraint_records_the_phrase_that_produced_it():
    compiled = compile_intent(TASK)
    for path, sentence in compiled.provenance.items():
        assert sentence in TASK
    assert compiled.provenance["public_api.change"] == "Do not change the public API."


def test_unenforceable_promises_are_reported_not_pretended():
    compiled = compile_intent(TASK)
    sentences = [s for s, _ in compiled.unenforceable]
    assert "Preserve Python 3.11 support." in sentences
    assert "not a checkable invariant" in dict(compiled.unenforceable)["Preserve Python 3.11 support."]


@pytest.mark.parametrize(
    "sentence,reason_fragment",
    [
        ("Keep the architecture simple and readable.", "subjective quality"),
        ("The result must be faster than before.", "benchmark oracle"),
    ],
)
def test_subjective_and_perf_goals_are_admitted_gaps(sentence, reason_fragment):
    compiled = compile_intent(sentence)
    assert reason_fragment in dict(compiled.unenforceable)[sentence]


def test_unrecognised_constraints_are_surfaced():
    compiled = compile_intent("Refactor auth.\nDo not upset the moon phase alignment.")
    assert compiled.unmatched == ["Do not upset the moon phase alignment."]
    assert compiled.constraints.get("protected_paths") is None


def test_a_plain_goal_never_becomes_a_constraint():
    """Inventing constraints from descriptive prose is how a compiler blocks work
    nobody asked it to block."""
    compiled = compile_intent("Refactor authentication.\nIt currently uses the public API and tests.")
    assert compiled.constraints == {}
    assert compiled.unmatched == []


def test_explicit_paths_are_protected():
    compiled = compile_intent("Refactor.\nDo not touch `src/legacy/**` or `config/prod.yaml`.")
    protected = compiled.constraints["protected_paths"]
    assert "src/legacy/**" in protected
    assert "config/prod.yaml" in protected


def test_scope_budgets():
    compiled = compile_intent("Refactor.\nChange at most 5 files and no more than 200 lines.")
    assert compiled.constraints["scope"]["max_files"] == 5
    assert compiled.constraints["scope"]["max_lines"] == 200


def test_forbidden_commands():
    compiled = compile_intent("Refactor.\nDo not push to the remote.")
    assert "git push*" in compiled.constraints["commands"]["forbidden"]


def test_spanish_phrasing_is_recognised():
    compiled = compile_intent("Refactoriza auth.\nNo cambies la API pública.\nNo añadas dependencias nuevas.")
    assert compiled.constraints["public_api"]["change"] == FORBIDDEN
    assert compiled.constraints["dependencies"]["additions"] == FORBIDDEN


def test_bullets_are_not_collapsed():
    text = "Refactor.\n- Do not change the public API\n- Do not add dependencies\n"
    assert len(split_sentences(text)) == 3
    compiled = compile_intent(text)
    assert "public_api.change" in compiled.provenance
    assert "dependencies.additions" in compiled.provenance


def test_duplicate_phrasings_do_not_duplicate_entries():
    compiled = compile_intent("Refactor.\nDo not touch the tests.\nNever modify tests.")
    assert compiled.constraints["protected_paths"].count("tests/**") == 1


def test_objective_can_be_overridden():
    assert compile_intent(TASK, objective="custom goal").objective == "custom goal"


def test_empty_input():
    compiled = compile_intent("")
    assert compiled.objective == ""
    assert compiled.constraints == {}
    assert compiled.coverage == 1.0


# ------------------------------------------------------------------ rendering


def test_render_is_readable_and_auditable():
    text = render(compile_intent(TASK))
    assert "objective: Refactor authentication." in text
    assert '# from: "Do not change the public API."' in text
    assert "- tests/**" in text
    assert "# NOT COMPILED" in text
    assert "Preserve Python 3.11 support." in text


def test_render_of_an_empty_compile_still_parses():
    text = render(compile_intent("Refactor auth."))
    assert "constraints:" in text


def test_render_includes_scope_and_commands():
    text = render(compile_intent("Refactor.\nChange at most 3 files.\nDo not push."))
    assert "max_files: 3" in text
    assert 'forbidden:' in text


# -------------------------------------------------------------- measured score

#: Labelled corpus. Each case is (task text, the constraint paths it should yield).
CORPUS: list[tuple[str, set[str]]] = [
    (TASK, {"public_api.change", "dependencies.additions",
            "protected_paths[tests/**]", "protected_paths[migrations/**]"}),
    ("Fix the bug.\nDo not change the public API.", {"public_api.change"}),
    ("Upgrade deps.\nDo not add new libraries.", {"dependencies.additions"}),
    ("Refactor.\nDon't modify the existing tests.", {"protected_paths[tests/**]"}),
    ("Refactor.\nAsk before touching the database schema.", {"protected_paths[migrations/**]"}),
    ("Refactor.\nDo not change the CI workflow.", {"protected_paths[.github/**]"}),
    ("Refactor.\nNever push to origin.", {"commands.forbidden[git push*]"}),
    ("Refactor.\nChange at most 4 files.", {"scope.max_files"}),
    ("Refactor.\nNo more than 50 lines.", {"scope.max_lines"}),
    ("Refactor.\nDo not add coupling between modules.", {"module_graph.new_edges"}),
    ("No cambies la API pública.", {"public_api.change"}),
    ("No añadas dependencias nuevas.", {"dependencies.additions"}),
    ("Add a feature.", set()),
    ("Refactor authentication. The tests already cover the public API.", set()),
    ("Rewrite the parser.\nIt should be faster.", set()),
]


def test_compiler_accuracy_is_measured_and_does_not_regress():
    """The number that decides whether this layer is trustworthy at all."""
    result = score(CORPUS)
    assert result["precision"] >= 0.95, result
    assert result["recall"] >= 0.95, result
    assert result["f1"] >= 0.95, result


def test_coverage_reports_what_was_understood():
    compiled = compile_intent(TASK)
    assert 0.0 < compiled.coverage < 1.0  # python 3.11 line is admitted as a gap
    assert compile_intent("Do a thing.").coverage == 1.0
