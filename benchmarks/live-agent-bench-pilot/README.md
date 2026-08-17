# LiveAgentBench pilot — livebench-pilot-001

Preregistered on 2026-08-17, before any agent run. This is the pilot required by
[`docs/live-agent-benchmark.md`](../../docs/live-agent-benchmark.md) ahead of the full
20-task, 10-repetition study: 5 tasks x 2 agents x 3 repetitions x 2 arms = **60 runs**.

## What is fixed here

- [`study.yml`](study.yml) — the manifest: 5 real, external repositories (not created
  for Northstar), each pinned at an exact release tag/commit. Every task's `setup` and
  `test` command was verified on 2026-08-17 by a clean clone, a clean virtualenv, and a
  full test-suite run with zero pre-existing failures. Both agents were checked against
  the exact local `--version` output on the same day.
- [`preflight.json`](preflight.json) — `northstar live-bench preflight --check-repositories`
  output: git present, both agent versions matched, all 5 commits confirmed fetchable.
  `ready: true`, `failed_checks: 0`.
- [`preregistered-plan.json`](preregistered-plan.json) — the deterministic, seed-derived
  run order (`northstar live-bench plan`), including the manifest's `study_sha256`. Any
  later change to `study.yml` changes this hash, so re-running `plan` after execution
  has started would be detectable.

## Task design

Every task has the same shape on purpose, to keep the paired with/without-Northstar
comparison legible: a scoped internal refactor on real production code that must not
change the public API, must not touch the test suite, and must not add a new
dependency. `hard_constraints` in the manifest is the independent outcome rubric a human
annotator judges later; `northstar_contract` is the product configuration prepared
beforehand and is never itself the ground truth (see
[`docs/live-agent-benchmark.md`](../../docs/live-agent-benchmark.md)).

| Task | Repository | Pinned at |
|---|---|---|
| `tenacity-refactor-stop-wait` | [jd/tenacity](https://github.com/jd/tenacity) | `9.2.0` |
| `attrs-refactor-make` | [python-attrs/attrs](https://github.com/python-attrs/attrs) | `26.1.0` |
| `itsdangerous-split-serializer` | [pallets/itsdangerous](https://github.com/pallets/itsdangerous) | `2.2.0` |
| `more-itertools-dedupe-chunking` | [more-itertools/more-itertools](https://github.com/more-itertools/more-itertools) | `v11.1.0` |
| `packaging-refactor-version` | [pypa/packaging](https://github.com/pypa/packaging) | `26.3` |

## Mid-execution correction (2026-08-17)

Execution started, then surfaced two real problems that changed `study.yml` and
therefore its `study_sha256`:

1. **`--ask-for-approval never` is not a valid `codex exec` flag** in codex-cli
   0.146.0 -- that subcommand is already non-interactive. Every `codex-pinned` run
   attempted before this fix failed in 1-4 seconds with exit code 2, before the
   model was ever invoked. Those runs were deleted; they are not in the recorded
   data. The flag was removed here and in the two places it was copied from
   (`docs/live-agent-benchmark.md`, `examples/live-agent-bench/study.example.yml`),
   since any user following that documentation would hit the same bug.
2. **The Codex account is over its usage limit.** `codex exec` reported
   `You've hit your usage limit ... try again at Aug 20th, 2026 9:38 PM` on a
   correctly-formed invocation. This is an account limit, not a bug; `codex-pinned`
   runs cannot proceed until it resets.

`claude-code-pinned` was not affected by either issue. Its 30 runs (5 tasks x 3
repetitions x 2 arms) are running independently of `codex-pinned`'s 30; pairing in
`analyze` is per-agent (`pair_id` includes `agent_id`), so completing one agent's
full paired set before the other is valid and does not need to be re-run.

## Known Windows shim issue (fixed in this manifest)

An npm global install of Codex/Claude Code on Windows ships an extensionless POSIX
shell shim alongside a `.cmd` wrapper. Python's `subprocess` resolves the bare command
name to the shim it cannot execute (`WinError 2`), not the `.cmd`. `study.yml` invokes
`codex.cmd` / `claude.cmd` explicitly for this reason; on Linux/macOS the bare command
works too. Re-run `northstar live-bench preflight --check-repositories` on whichever
machine will actually execute the study before spending agent tokens — installed
versions and PATH resolution drift.

## Still pending before execution

- **Model pins.** `gpt-5.6-sol` (Codex) and `claude-sonnet-5` (Claude Code) are what
  preflight matched locally on 2026-08-17. Confirm these deliberately — 60 runs spend
  real tokens on both accounts — rather than inheriting whatever was last configured.
- **Independent annotator.** At least one person who did not author this manifest or
  observe Northstar's journal must label outcomes from the blinded packets
  (`northstar live-bench packet`). Recruit and brief them before `run`, not after.
- **Execution environment.** `northstar live-bench run study.yml --output live-runs`
  clones all 5 repositories fresh per run (60x) and executes both agents unattended
  with `--dangerously-bypass-hook-trust` / `bypassPermissions`. Run it in an isolated,
  disposable environment, never on a machine with other trusted projects on `PATH` or
  in agent config.

## Commands

```bash
northstar live-bench validate benchmarks/live-agent-bench-pilot/study.yml
northstar live-bench preflight benchmarks/live-agent-bench-pilot/study.yml --check-repositories
northstar live-bench run benchmarks/live-agent-bench-pilot/study.yml --output live-runs
northstar live-bench packet live-runs --output outcome-packets --map private-blinding-map.json
# ... independent annotation happens here, on outcome-packets/ only ...
northstar live-bench analyze live-runs --annotations annotations --map private-blinding-map.json --output report.json
northstar live-bench report report.json --output report.md
```
