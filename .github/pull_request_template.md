## What changed

<!-- Describe the smallest coherent change. -->

## Why

<!-- State the failure mode, evidence gap, or user impact. -->

## Validation

- [ ] `python -m pytest --cov=northstar --cov-report=term-missing`
- [ ] `ruff check src tests`
- [ ] Relevant IntentDriftBench trajectory or clean control added/updated
- [ ] `python -m build --sdist --wheel` and `python -m twine check dist/*` when packaging changes
- [ ] README, SECURITY and ROADMAP claims remain no stronger than the evidence

## Security boundary

<!-- If trusted state, hooks, tools, approval or replay changed, describe fail-closed and adversarial tests. -->
