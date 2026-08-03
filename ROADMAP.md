# Roadmap

Ordered by what most limits the project's central claim, not by what is easiest.

## Shipped (v0.1)

- Frozen oracle at t=0: file hashes, API surface, dependency set, module graph.
- Six deterministic checks, all state-versus-baseline.
- Behavioural oracle: test outcomes frozen as an executable witness.
- Human-signed amendments with partial re-baseline.
- Rule-based intent compiler with measured precision/recall.
- Claude Code and Codex adapters, zero configuration.
- IntentDriftBench: 12 trajectories, 7 metrics, re-run by CI on every commit.

## Next

### Live-agent benchmark numbers

The benchmark's trajectories are scripted. That measures what the runtime *catches*,
not how often a real model drifts — and the second number is the interesting one.
`bench.from_journal()` already replays a real session, so the missing piece is a
harness that drives Claude Code and Codex through a fixed task set and collects the
journals.

Blocking on: a task set that is realistic without being enormous.

### Prevention beyond protected paths

The pre-tool gate blocks path writes before they land. A changed signature or an
added dependency can currently only be *detected* after the edit, which is why the
violation rate in the benchmark falls to 42% rather than to 0%. Gating on the
*content* of a proposed edit — parsing the new text before it is written — would
close that gap.

### Tree-sitter grammars

JS/TS, Go, Rust and Java use pattern extractors today. They see the declarations
people actually write and miss the clever ones. Real grammars make those surfaces
exact instead of heuristic. Worth doing the moment a real project reports a miss.

## Considered and deferred

**LLM intent compiler.** A model translating prose to YAML mistranslates, and a wrong
contract blocks for the wrong reason — which costs more trust than it buys. It ships
if and when its translation accuracy is a measured number that beats the rule-based
compiler on the same corpus, not before.

**Semantic drift judging.** Asking a model at step 50 whether step 50 looks off-course
puts the judge in the same degraded context as the agent it judges. Northstar's whole
design is the opposite bet. If this is ever added it will be advisory-only, and it
will never be able to block on its own.

**Rollback.** Northstar reports where the trajectory left the contract. Undoing it is
git's job, and wrapping git here would be a worse git.

**A SaaS console.** Not before the local runtime is worth using.
