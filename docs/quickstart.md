# Northstar quickstart

This walkthrough takes a repository from ungoverned to an observable, reversible
Northstar installation. Read [SECURITY.md](../SECURITY.md) before treating an agent as
adversarial: Northstar is a deterministic guardrail, not an operating-system sandbox.

## 1. Install and prove the executable

Northstar requires Python 3.11 or newer. Until `0.2.x` is published to PyPI, install
the source directly from GitHub:

```bash
pipx install "git+https://github.com/Jairogelpi/northstar.git"
# or
uv tool install "git+https://github.com/Jairogelpi/northstar.git"

northstar demo
```

The demo is deliberately disposable. It uses a temporary checkout and exercises the
real contract, authority, gate, check, request and approval code paths.

## 2. Preview the initial contract

From the root of the repository you want to govern:

```bash
northstar init "refactor authentication" --dry-run
```

The preview lists every active invariant. A plain objective is not semantically
translated into hidden restrictions; it uses the conservative default profile.

To compile explicit constraints from a task, put the complete task in `TASK.md`:

```text
Refactor authentication.
Do not modify existing tests.
Do not change the public API.
Do not add runtime dependencies.
```

Then preview it:

```bash
northstar init --from-task TASK.md --dry-run
```

Northstar records the source sentence for every compiled rule. If it sees an unmatched
or unenforceable constraint-like sentence, it prints `NOT COMPILED` and stops. Review
those lines manually; only then rerun with `--accept-uncompiled` if the remaining risk
is acceptable.

## 3. Freeze and wire the agents

```bash
northstar init --from-task TASK.md
```

The interactive command asks you to create an approval passphrase. It is never stored;
it encrypts the amendment signing key. The command then:

- freezes repository files, APIs, dependencies and module edges;
- optionally records baseline test outcomes when `--behavior` is present;
- creates an HMAC-sealed canonical authority in the OS data directory;
- writes reviewable `.northstar/` mirrors;
- installs Claude Code and Codex project wiring without replacing unrelated settings.

For Codex, open `/hooks`, inspect the exact project hook and trust it. Northstar can
verify the hook file but cannot read Codex's user-local trust decision.

## 4. Diagnose before the first run

```bash
northstar doctor
northstar doctor --strict
```

`doctor` checks the runtime, sealed authority, mirrors, wiring, current tree verdict,
observed hook activity and installed agent executables. It is read-only. The normal
mode returns success for warnings so a newly installed project can be inspected;
`--strict` turns warnings into a non-zero exit for CI or automation.

## 5. Work and recover context

```bash
northstar status
northstar check
```

Run `status` after compaction or a handoff. It reads the original objective and current
verdict from authenticated state rather than relying on conversational memory.

When an invariant must legitimately change, the agent creates an untrusted request:

```bash
northstar request \
  --grant public_api:src/auth.py::login \
  --reason "The reviewed design adds the tenant argument."
```

A human reviews that exact grant and approves it in a separate interactive terminal:

```bash
northstar approve REQUEST_ID
```

The signed amendment widens only the named rule. Inspect the final chain with:

```bash
northstar receipt
```

## 6. Remove Northstar safely

Stop active agents, ensure the repository is clean and run:

```bash
northstar uninstall --agent all
```

Governed-project removal requires the approval passphrase. It removes only Northstar's
managed hook entries and instruction block; unrelated Claude settings, Codex hooks and
`AGENTS.md` content are preserved. You can also target `claude` or `codex`.

The sealed authority remains available for audit and recovery. Removing that external
state is a separate, deliberate administrative action.

## Common failures

| Symptom | Meaning | Next action |
|---|---|---|
| `NOT COMPILED` | A sentence was not translated into a deterministic rule | Review it and edit the contract/task; accept explicitly only if appropriate |
| `INTEGRITY_FAILURE` | Authority, mirrors or wiring no longer match | Stop the agent, inspect changes, repair or deliberately reinitialise |
| Codex hook file passes but no activity is observed | Project hook may not be trusted | Inspect and trust it through `/hooks`, then run one harmless action |
| Agent executable warning | The integration is configured but its CLI is unavailable | Install that agent or remove the unused integration |
| Behaviour capture fails | The configured test command could not produce a baseline | Fix the test command before enabling `--behavior` |

