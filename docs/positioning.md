# Product positioning

## One sentence

Northstar is the deterministic intent-fidelity layer that keeps coding agents inside
the human-approved invariants of a long task.

## The problem it owns

Coding agents already have instructions, memory, tests and sandboxes. None of those
answers this narrower question by itself:

> Does the complete repository still satisfy the non-negotiable constraints agreed at
> the start of this task, including every explicitly approved change of mind?

That is the category Northstar calls **intent fidelity**. The failure mode is
**intent drift**: a cumulative divergence that can emerge from individually plausible
steps, especially after compaction, handoffs and long repair loops.

## Category boundaries

| Layer | Primary question | Northstar relationship |
|---|---|---|
| Prompt and project instructions | What should the agent do? | Input, not enforcement |
| Context memory / compaction | What should the agent remember? | Helpful, but shares the agent's changing context |
| Permission prompts / tool firewalls | May this command or capability run? | Complementary pre-action boundary |
| Sandbox | What can this process reach? | Required for hostile-process isolation |
| Tests and CI | Does selected behaviour pass? | Optional behavioural witness and downstream validation |
| LLM reviewer | Does this change look reasonable? | Useful advisory judgement, not a deterministic oracle |
| **Northstar** | Does the whole tree still match the approved invariant baseline? | The intent-fidelity layer |

Northstar is strongest when these layers are composed. It does not claim to replace
instructions, tests or an OS sandbox.

## Defensible differentiation

### Judgement happens once

A semantic judge repeatedly reinterprets intent and can share the same degraded
context as the agent. Northstar makes the policy decision at `t=0`, while the prompt is
fresh and the human is present, then freezes it into deterministic artifacts.

### State beats delta

A tool-call check asks whether one proposed action is dangerous. Northstar also checks
the complete tree against the original baseline. This catches cumulative drift and
prevents intermediate edits from laundering a violation.

### Authority is not the working tree

Repository configuration is writable by the very process it governs. Northstar keeps
canonical state outside that tree and seals both canonical artifacts and reviewable
mirrors. Corruption and wiring loss fail closed.

### Change of mind is first-class

A rigid invariant system is abandoned the first time requirements change. Northstar
models that change as a one-time, human-signed, narrowly scoped grant in an auditable
amendment chain.

### Unknown means unknown

Northstar does not turn parser failure or unsupported language syntax into `ALLOW`.
Uncovered input remains explicit, making claim boundaries inspectable.

## Who it is for

Northstar is currently best suited to:

- maintainers delegating long refactors with explicit compatibility constraints;
- teams evaluating agent autonomy while preserving tests, APIs or dependencies;
- security-conscious experiments that already run agents inside a suitable sandbox;
- researchers studying cumulative constraint violations in agent trajectories.

It is not yet the right choice for users who need a hosted dashboard, broad language-
exact semantic coverage, or a proven malicious-process isolation boundary.

## Message hierarchy

1. **Outcome:** long coding-agent runs stay faithful to the approved task invariants.
2. **Mechanism:** deterministic whole-tree comparison against an external sealed
   baseline.
3. **Proof:** reproducible scripted evidence today; independently labelled live-agent
   evidence only when published.
4. **Trust:** explicit unknowns, signed exceptions and documented security limits.

Avoid positioning Northstar as “AI safety” in the abstract. The concrete wedge—intent
fidelity for coding-agent repository changes—is narrower, testable and differentiated.

