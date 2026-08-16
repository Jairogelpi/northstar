## What changed

<!-- Lead with the user-visible outcome. -->

## Why

<!-- Name the failure mode, evidence gap or usability problem. -->

## Verification

- [ ] `ruff check src tests`
- [ ] `python -m pytest --cov=northstar --cov-report=term-missing`
- [ ] `northstar demo --json`
- [ ] `northstar bench --json` (when enforcement or evidence changes)
- [ ] Documentation and claim boundaries updated

## Trust review

- [ ] Unsupported input remains `UNKNOWN`, not `ALLOW`
- [ ] No fallback from external authority to working-tree state
- [ ] New governance mutations require an interactive human path
- [ ] Benchmark outcomes are not derived from Northstar findings
