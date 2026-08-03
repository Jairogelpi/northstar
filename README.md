# Northstar

[![CI](https://github.com/Jairogelpi/northstar/actions/workflows/ci.yml/badge.svg)](https://github.com/Jairogelpi/northstar/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-98%25-brightgreen)](https://github.com/Jairogelpi/northstar/blob/main/pyproject.toml)
[![Silent drift](https://img.shields.io/badge/silent%20drift-0%25-brightgreen)](#intentdriftbench)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)](https://github.com/Jairogelpi/northstar/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

**Executable intent contracts for coding agents.**

> Coding agents remember your prompt. Northstar verifies that their actions still obey it.

You ask for *"refactor authentication, don't change the public API."* Fifty steps
later the API has changed, the tests were edited to agree, and every individual
edit looked reasonable. That is not forgetfulness. It is **intent drift**: the
constraints, priorities and meaning of the original request decaying across a long
trajectory.

Northstar freezes what must not change into a deterministic oracle **at t=0**, then
checks every step against that baseline — from a process that has never seen the
agent's conversation.

```
$ northstar init "refactor authentication"
northstar: contract v1 for "refactor authentication"
  baseline frozen: 41 files, 118 public symbols, 6 runtime deps
  wired: settings.json, CLAUDE.md, AGENTS.md, config.toml

# ...the agent works...

Edit tests/test_auth.py       -> DENY   protected file
Edit pyproject.toml           -> DENY   runtime dependency added: httpx
Edit auth/service.py          -> ALLOW
  (signature of login changed) -> DENY   public_api: (user, password) -> (user, password, tenant)
```

---

## Why this is not another hook wrapper

Three design decisions, each targeting a specific way existing guardrails fail.

### 1. The judge does not share context with the agent

Every semantic drift detector shipped today asks an LLM, at step 50, whether step 50
looks off-course. That judge sits in the same degraded context as the agent it is
judging, so it drifts alongside it — and fails exactly when it is needed most.

Northstar spends its judgement **once, at t=0**, when the prompt is fresh and the
human is present, and freezes the result into artifacts a dumb process can re-check
forever: an API surface snapshot, a dependency set, a module graph, file hashes.
At step 500 the check is `sha256` and an AST diff. No opinion required.

### 2. State, not delta

Other tools ask *"does this edit violate anything?"*. Drift does not live in any single
edit — fifty innocent edits produce an unfaithful result with no blockable step among
them.

Every Northstar check compares the **whole tree against the baseline**, always. That
makes violations monotone: no sequence of intermediate steps can launder one. An agent
that breaks the API at step 12 and "restores" it differently at step 40 is caught at both.

### 3. The agent cannot touch its own grader

`.northstar/` is protected by construction, not by policy — it cannot be disabled by
the contract it governs. `northstar amend` from an agent shell is denied outright.
An agent may *request* a grant. Only a human signs one.

Making the test pass by editing the test is an architecture failure, not a behaviour
problem. So it is fixed in the architecture.

---

## Install

```bash
pip install northstar-runtime
cd your-project
northstar init "what this run is meant to achieve"
```

That is the whole setup. `init` writes the contract, freezes the baseline, and wires
both agents. No configuration file to hand-write.

| Agent | Wiring | Enforcement |
|---|---|---|
| **Claude Code** | `.claude/settings.json` (PreToolUse + PostToolUse) | **Blocks** the write before it happens, plus trajectory checks after |
| **Codex** | `AGENTS.md` + `.codex/config.toml` | Post-hoc detection + instructions; Codex exposes no blocking pre-tool hook |

Both are honest about what they are. Detection without prevention is still worth
having, but it is not the same guarantee, and Northstar will not pretend otherwise.

---

## The contract

A **deny-list, not a specification**. You declare what must not break — usually four
lines, usually things you already know. The freezer derives the hundreds of assertions
from the repository itself. Anything the contract does not name is free, so there is no
waterfall and no penalty for a short contract.

```yaml
objective: refactor authentication

constraints:
  protected_paths:
    - tests/**

  public_api:
    change: forbidden        # forbidden | approval_required | allowed
    additions: allowed
    scope: ["**/*.py"]

  dependencies:
    additions: forbidden

  module_graph:
    new_edges: allowed

  scope:
    max_files: 0             # 0 = no budget
    max_lines: 0

  commands:
    forbidden: ["git push*", "rm -rf*"]
```

### The behavioural oracle

"Do not change the expected behaviour" is the constraint people actually care about,
and no hash expresses it. The trick is not to judge it semantically at step 50 — it is
to capture it as an **executable witness at step 0**, while the baseline is still the
thing everyone agreed on.

```yaml
  behavior:
    change: forbidden
```

`northstar init --behavior` runs your test suite once at freeze time and records the
outcome of every test. Later, the same suite re-runs and the outcomes are compared.
The semantic question becomes a deterministic one:

> `login()` used to return `True` for a valid user, and the test that says so used to
> pass. It still has to.

Two things make this honest. It captures the behaviour that **exists**, not the
behaviour someone hoped for — a test failing at baseline is frozen as failing, and
making it pass is reported as a change like any other, for the human to bless or not.
And the witness lives outside the working tree while the test files are protected, so
the agent cannot make the oracle agree with it.

Off by default: it costs a full test run at freeze time, and a check that makes `init`
slow is a check people turn off.

### Verdicts

| | |
|---|---|
| `ALLOW` | on course |
| `WARN_DRIFT` | diverging, not yet blocking |
| `UNKNOWN` | **not covered** — a file could not be parsed, and we say so |
| `REQUIRE_APPROVAL` | needs a human signature |
| `DENY` | blocked |

`UNKNOWN` is a first-class outcome. Claiming coverage we do not have is the failure
mode that makes a guardrail worse than none.

---

## When you change your mind

You will. Mid-task the human learns something, and the original constraint turns out
to be wrong. That is the **central case, not the exception**.

A red check is not a wall — it is the moment the system discovers the initial intent
was incomplete and asks for the decision only a human can make:

```
NORTHSTAR: this action diverges from the intent contract.

Objective (contract v1): "refactor authentication"

  [DENY] public_api: signature changed: (user, password) -> (user, password, tenant)
      grant needed: public_api:src/auth/service.py::login

Either take another route, or stop and ask the human to sign:
    northstar amend --grant "public_api:src/auth/service.py::login" --reason "..."
```

```bash
northstar amend --grant "public_api:src/auth/service.py::login" --reason "multi-tenant agreed"
```

Three rules keep "the human can change their mind" from degrading into "the contract
means nothing":

1. **Partial re-baseline.** Signing re-freezes only the named grant. Every other
   invariant stays frozen against the original baseline. Otherwise each signature
   would be a general amnesty — the classic failure where one exception is read as
   permanent permission.
2. **The human signs, the agent asks.** If the agent could amend, the contract would
   only ever mean what the agent currently wants it to mean.
3. **The chain is kept.** `v1 -> v2 (signed, step 34, reason) -> v3`. At the end you
   see not just what was built, but where the original intent turned out to be wrong
   and who decided that.

**Drift is any divergence from the contract that was not signed.** That definition is
what turns a fuzzy psychological problem into a version-control one.

---

## Commands

```bash
northstar init "<objective>"     # contract + baseline + agent wiring
northstar init --from-task task.md --behavior   # compile constraints, freeze test outcomes too
northstar compile "<task text>"  # translate a description into constraints, with provenance
northstar check                  # verify the tree against the baseline (exit 1 if blocking)
northstar status                 # restate the objective and the live verdict
northstar amend --grant K:ID --reason "..."   # human-signed, scoped widening
northstar freeze                 # deliberately re-baseline everything
northstar receipt                # bind contract, baseline, decisions, amendments
northstar bench                  # run IntentDriftBench
northstar show                   # print the contract
```

Run `northstar status` after any context compaction or handoff. It restates the
objective from disk rather than from a memory that has been summarised twice.

---

## The receipt

```json
{
  "objective": "refactor authentication",
  "contract_version": 2,
  "base_commit": "a1b2c3...",
  "final_verdict": { "decision": "ALLOW" },
  "amendments": [
    { "version": 2, "reason": "multi-tenant agreed", "grants": ["public_api:...::login"] }
  ],
  "uncovered_files": [],
  "metrics": { "steps": 61, "decisions": { "DENY": 3 }, "wasted_steps": 0 }
}
```

`wasted_steps` is the metric that matters over long runs. Blocking at step 50 saves
correctness but burns 40 steps of work. The goal is not just to catch drift — it is to
catch it at the step that caused it.

---

---

## The intent compiler

`northstar compile` turns a task description into constraints — and shows its
workings, because a compiler trusted without review is just a slower way to guess.

```bash
northstar compile --file task.md
```

```yaml
objective: Refactor authentication.

constraints:
  protected_paths:
    - tests/**  # from: "Do not modify the existing tests."
    - migrations/**  # from: "Ask before changing the database schema."

  public_api:
    change: forbidden  # from: "Do not change the public API."

  dependencies:
    additions: forbidden  # from: "Do not add runtime dependencies."

# NOT COMPILED -- these are on you, not on the runtime:
#   "Preserve Python 3.11 support."
#       python version support is not a checkable invariant yet
#   "Keep the architecture simple."
#       subjective quality bars cannot be frozen deterministically
```

It is **rule-based, not model-based**. A model translating prose into YAML can
mistranslate, and a wrong contract blocks for the wrong reason — which costs more
trust than it buys, because the entire value of the deterministic layer is that its
refusals are never arguable. So the compiler matches only phrasings it recognises,
records the source sentence next to every constraint, and reports what it did not
understand instead of guessing.

Its accuracy is a measured number: **precision 1.00, recall 0.95** on a labelled
corpus (English and Spanish), enforced by `tests/test_compiler.py` so it cannot
silently regress. `northstar init --from-task task.md` compiles and freezes in one go.

---

## IntentDriftBench

Twelve trajectories — ten adversarial, two clean controls — each replayed twice, with
and without the runtime enforcing.

```bash
northstar bench
```

| Metric | Without runtime | With runtime |
| --- | ---: | ---: |
| Hard-constraint violation rate | 75% | 42% |
| Silent drift rate | 75% | **0%** |
| False block rate | 0% | 0% |
| Human escalation rate | 0% | 8% |
| Task completion rate | 100% | 100% |
| Detection latency (steps) | 0.0 | 0.0 |
| Runtime overhead (s/step) | 0.0001 | 0.0033 |

<sub>Reproduced by CI on every commit (ubuntu-latest, Python 3.12). Overhead is
roughly 10× higher on Windows — it is dominated by filesystem walks.</sub>

Read the second row, then the first. **Silent drift goes to zero** — nothing reaches
the final tree unannounced. But the violation rate only falls to 42%, and that is not
a rounding error: the pre-tool gate *prevents* path writes, while a changed signature
or an added dependency can only be *detected* after the edit lands. Detection plus a
blocked exit code is what the agent gets; undoing the edit is the agent's move, or
git's. Reporting 0% there would be a lie.

The controls matter as much as the attacks: a runtime that blocked everything would
score perfectly on violations. **0% false blocks and 100% task completion** are what
say it is usable.

**What the benchmark does not claim.** These trajectories are scripted, not sampled
from live agents, so the numbers say what the runtime *catches* — not how often a
given model drifts. `bench.from_journal()` replays a real session's journal as a
trajectory, which is the honest path to live-agent numbers.

---

## What this does not do

Stated plainly, because overclaiming here is the whole disease:

- **No proof of semantic intent.** The behavioural oracle covers "does it still do
  what it did", because that is expressible as frozen test outcomes. "Keep the
  architecture simple" is not, and comes back as `UNKNOWN` rather than a guess.
- **Pattern extractors outside Python.** Python surfaces are exact (AST). JavaScript,
  TypeScript, Go, Rust and Java are matched with patterns, which genuinely misses
  exotic declarations — so findings from them say `heuristic extractor` in the text,
  and a language with no extractor at all becomes `UNKNOWN` rather than an empty
  surface that can never be violated. Tree-sitter grammars are the upgrade path.
- **Live-agent benchmark numbers.** See above.
- **No rollback.** Northstar tells you where the trajectory left the contract. Undoing
  it is git's job.

## Development

```bash
pip install -e ".[dev]"
pytest --cov=northstar --cov-report=term-missing
```

290 tests, 98% coverage, enforced at 95% in `pyproject.toml`. CI runs the suite on
Ubuntu and Windows across Python 3.11–3.13, and **re-runs the benchmark on every
commit** — if silent drift stops being 0% or false blocks appear, the build fails.
Published numbers that are not re-verified are marketing.

`tests/test_drift_scenarios.py` drives ten ways an agent silently stops obeying you,
end-to-end through the same hook a real agent hits.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the one rule that governs new checks, and
[ROADMAP.md](ROADMAP.md) for what is deliberately deferred and why.

## License

Apache-2.0
