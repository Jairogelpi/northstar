# Northstar

[![CI](https://github.com/Jairogelpi/northstar/actions/workflows/ci.yml/badge.svg)](https://github.com/Jairogelpi/northstar/actions/workflows/ci.yml)
[![Coverage gate](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen)](https://github.com/Jairogelpi/northstar/blob/main/pyproject.toml)
[![Scripted silent drift](https://img.shields.io/badge/scripted%20silent%20drift-0%25-brightgreen)](#intentdriftbench)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)](https://github.com/Jairogelpi/northstar/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

**A deterministic invariant enforcement runtime for coding agents.**

> Coding agents remember your prompt. Northstar verifies that their actions still obey it.

You ask for *"refactor authentication, don't change the public API."* Fifty steps
later the API has changed, the tests were edited to agree, and every individual
edit looked reasonable. That is not forgetfulness. It is **intent drift**: the
constraints, priorities and meaning of the original request decaying across a long
trajectory.

Northstar freezes what must not change into a deterministic oracle **at t=0**, stores
the trusted bundle outside the working tree, and checks every step against that
baseline from a process that has never seen the agent's conversation.

```
$ northstar init "refactor authentication"
northstar: contract v1 for "refactor authentication"
  baseline frozen: 41 files, 118 public symbols, 6 runtime deps
  wired: settings.json, CLAUDE.md, AGENTS.md, hooks.json

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

An LLM-based semantic drift detector can ask, at step 50, whether step 50 looks
off-course while sharing the agent's degraded context. That coupling makes its
judgement vulnerable to the same compaction and reinterpretation as the run itself.

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

### 3. The working tree is not the authority

`.northstar/` contains readable mirrors. The canonical contract, oracle, journal and
project metadata live under the OS data directory and are HMAC-sealed. Amendments are
signed with an Ed25519 key whose private half is encrypted by a human approval
passphrase. Missing or
corrupt authority, mirrors, or hook wiring is a blocking `INTEGRITY_FAILURE`; it never
means “this project is ungoverned”.

In the supported hooked workflow, an agent may create an untrusted approval request
but cannot change the live contract through an ordinary tool action.
A human consumes the request once from a separate interactive terminal, and the
resulting amendment is authenticated and added to the chain.

Making the test pass by editing the test is an architecture failure, not a behaviour
problem. So it is fixed in the architecture.

---

## Install

```bash
pip install northstar-runtime
cd your-project
northstar init "what this run is meant to achieve"
```

`init` asks the human to create an approval passphrase, writes readable mirrors,
freezes the baseline, creates the external authority, and wires both agents. No
configuration file to hand-write. The passphrase is not stored; losing it means
requests cannot be approved. Recovery requires stopping agents, backing up and
removing the external authority manually, reviewing the readable mirrors, and running
the explicit migration flow below with a new passphrase.

Upgrading a v0.1 checkout is deliberately explicit because its local YAML was not a
trusted authority. Review `.northstar/contract.yaml` and `.northstar/oracle.json`, then
run from a human terminal:

```bash
northstar migrate --accept-existing-state
```

Existing amendments are re-authenticated by the new Ed25519 key and attributed to the
OS user performing the migration. No local fallback remains afterward.

| Agent | Wiring | Enforcement |
|---|---|---|
| **Claude Code** | `.claude/settings.json` (PreToolUse + PostToolUse) | **Blocks** the write before it happens, plus trajectory checks after |
| **Codex** | `AGENTS.md` + `.codex/hooks.json` (PreToolUse + PostToolUse) | **Blocks** inspected writes before execution, plus trajectory checks after |

Codex requires the human to review and trust project hooks through `/hooks`; an
untrusted hook is not active merely because the file exists. Both integrations retain
post-state checks because no pre-tool parser is a complete enforcement boundary.

After `northstar init` or `northstar install --agent codex`, open `/hooks` in Codex,
inspect the root-bound Northstar command, and trust it. Northstar can verify the hook
file and command but cannot inspect Codex's user-local trust decision.

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

  tools:
    unknown: approval_required  # unknown/MCP capabilities fail closed
    read_only: []               # tool-name globs reviewed by a human
    mutating: []
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
The witness lives in the sealed external authority while the test files are protected.
Tampering through a normal agent action is blocked or detected; the same-user shell
limit is described precisely in [SECURITY.md](SECURITY.md).

Off by default: it costs a full test run at freeze time, and a check that makes `init`
slow is a check people turn off.

### Verdicts

| | |
|---|---|
| `ALLOW` | on course |
| `WARN_DRIFT` | diverging, not yet blocking |
| `UNKNOWN` | **not covered** — a file could not be parsed, and we say so |
| `REQUIRE_APPROVAL` | needs a human approval |
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

Either take another route, or create a request for the human:
    northstar request --grant "public_api:src/auth/service.py::login" --reason "..."
```

```bash
# Safe for the agent: this does not change the contract.
northstar request --grant "public_api:src/auth/service.py::login" --reason "multi-tenant agreed"

# Human, in a separate interactive terminal:
northstar approve <request-id>
```

Three rules keep "the human can change their mind" from degrading into "the contract
means nothing":

1. **Scoped widening.** Approval authorises only the named grant. Every other
   invariant stays frozen against the original baseline. Otherwise each approval
   would be a general amnesty — the classic failure where one exception is read as
   permanent permission.
2. **The human approves, the agent requests.** Approval requires the signing
   passphrase from a separate interactive TTY; `--signed-by` and direct
   non-interactive amendment do not exist.
3. **The chain is authenticated.** `v1 -> v2 (approval id, signer, reason) -> v3`. At the end you
   see not just what was built, but where the original intent turned out to be wrong
   and who decided that.

**Drift is any divergence from the contract that was not approved.** That definition is
what turns a fuzzy psychological problem into a version-control one.

---

## Commands

```bash
northstar init "<objective>"     # contract + baseline + agent wiring
northstar init --from-task task.md --behavior   # compile constraints, freeze test outcomes too
northstar migrate --accept-existing-state  # reviewed v0.1 bundle -> R1 authority
northstar compile "<task text>"  # translate a description into constraints, with provenance
northstar check                  # verify the tree against the baseline (exit 1 if blocking)
northstar status                 # restate the objective and the live verdict
northstar request --grant K:ID --reason "..." # untrusted request; contract unchanged
northstar approve REQUEST_ID     # human-only, interactive, one-time approval
northstar freeze --reason "..."  # human-confirmed full re-baseline
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

The current evidence is a **15-example labelled regression corpus** (English and
Spanish) in `tests/test_compiler.py`. It guards known phrasing from regressing; it is
not a held-out accuracy estimate, and broader adversarial and third-party evaluation
is still pending. `northstar init --from-task task.md` compiles and freezes in one go.

---

## IntentDriftBench

Twenty-four scripted trajectories — twenty-two adversarial and two clean controls — each
replayed twice, with and without the runtime enforcing. Integrity attacks include
shell deletion, `python -c`, `sed -i`, `mv`, heredocs, symlink/path traversal, nested
working directories, an unknown MCP writer, hook deletion, direct contract API use,
and self-rebaseline.

```bash
northstar bench
```

| Metric | Without runtime | With runtime |
| --- | ---: | ---: |
| Hard-constraint violation rate | 92% | 21% |
| Silent drift rate | 92% | **0%** |
| False block rate | 0% | 0% |
| Human escalation rate | 0% | 4% |
| Task completion rate | 100% | 100% |
| Detection latency (steps) | 0.0 | 0.0 |
| Runtime overhead (s/step) | typically <0.0001 | typically 0.002–0.004 |

<sub>Reproduced by CI on every commit (ubuntu-latest, Python 3.12). Overhead is
roughly 10× higher on Windows — it is dominated by filesystem walks.</sub>

Read the second row, then the first. **Silent drift is zero in this scripted corpus** —
nothing reaches the final tree unannounced. But the violation rate is not zero: the
pre-tool gate *prevents* inspected path writes, while a changed signature
or an added dependency can only be *detected* after the edit lands. Detection plus a
blocked exit code is what the agent gets; undoing the edit is the agent's move, or
git's. Reporting 0% there would be a lie.

The controls matter as much as the attacks: a runtime that blocked everything would
score perfectly on violations. **0% false blocks and 100% task completion** are what
say it is usable.

**What the benchmark does not claim.** These trajectories are product-authored and
scripted, not sampled from live agents or independently labelled. The numbers are
regression evidence, not an external effectiveness estimate. Content-complete replay
is opt-in with `NORTHSTAR_CAPTURE_REPLAY=1`; verdict-only legacy journals are rejected
rather than converted into fake empty-file actions. The live-agent protocol remains
future evidence, not a shipped result.

The pre-registered study design and required run record are documented in
[LiveAgentBench protocol](docs/live-agent-benchmark.md).

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
- **A same-user security boundary.** External state and HMAC seals make tampering
  evident and fail closed. They cannot stop a process with unrestricted access as the
  same OS user from eventually reaching the authority key. Use an OS sandbox or
  separate service account when that is in scope; see [SECURITY.md](SECURITY.md).
- **No rollback.** Northstar tells you where the trajectory left the contract. Undoing
  it is git's job.

## Development

```bash
pip install -e ".[dev]"
pytest --cov=northstar --cov-report=term-missing
```

Coverage is enforced at 95% in `pyproject.toml`. CI runs the suite on Ubuntu and
Windows across Python 3.11–3.13, re-runs the scripted benchmark, builds wheel and
sdist, validates metadata, and clean-installs the wheel on every change. Tag releases
use PyPI trusted publishing and attach distributions, an SBOM, and build provenance.

The drift, policy, authority and benchmark tests exercise both ordinary invariant
breaks and integrity attacks end-to-end through the same hook a real agent hits.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the one rule that governs new checks, and
[ROADMAP.md](ROADMAP.md) for what is deliberately deferred and why.

## License

Apache-2.0
