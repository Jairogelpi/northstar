# Contributing

```bash
git clone https://github.com/Jairogelpi/northstar
cd northstar
pip install -e ".[dev]"
pytest --cov=northstar --cov-report=term-missing
```

Coverage is gated at 95% in `pyproject.toml`, so a bare `pytest` run fails the
build if a change is untested.

## The one rule

**Never let a check claim coverage it does not have.**

A guardrail that silently passes is worse than no guardrail, because people stop
reading the diff. If a check cannot verify something — an unparseable file, a test
runner that will not start, a language with no extractor — the answer is `UNKNOWN`,
never `ALLOW`. Every existing check follows this; new ones must too.

## Adding a check

1. Add the finding kind in `checks.py`.
2. Write the check as `check_x(contract, oracle, state) -> list[Finding]`. Compare
   against the **baseline**, never against the previous step — that is what makes
   violations monotone.
3. Map the kind to a contract rule in `policy._RULE_FOR_KIND`, or handle it
   explicitly in `judge()`.
4. Add it to `ALL_CHECKS`.
5. Add a trajectory to `bench.default_cases()`. A check with no benchmark case is a
   claim with no evidence.

Findings from a check that cannot be exact should say so in their `detail`, the way
the heuristic extractors do.

## Adding a language

Write an extractor in `surface.py` returning `Surface(symbols, HEURISTIC)` and
register it in `EXTRACTORS`. Pattern-based is fine — declare the ceiling rather than
hiding it. Do not register a language you cannot extract: `None` becomes `UNKNOWN`,
which is the correct answer.

## Adding a compiler rule

Add a `Rule` in `compiler.py`, then add labelled cases to `CORPUS` in
`tests/test_compiler.py`. Precision and recall are asserted at 0.95 and must not
regress. A rule that fires on descriptive prose is worse than a missing rule: it
blocks work nobody asked to block.

## Style

- Match the surrounding code. Comments explain *why*, not *what*.
- No new runtime dependencies without a strong reason. The package ships with one.
- Tests are named for the behaviour they pin, not the function they call.

## Reporting a false block

The most valuable bug report this project can get. Include the contract, the action
that was refused, and why it should have been allowed. False blocks are tracked as a
benchmark metric precisely so they cannot be dismissed as user error.
