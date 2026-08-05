# Roadmap

Ordered by the evidence missing from the product's central claim, not by feature count.

## R1 integrity boundary (v0.2, implemented)

- External canonical authority with readable working-tree mirrors.
- HMAC-sealed contract, oracle, journal, metadata and mirror digests.
- Fail-closed loading for deletion, corruption, partial writes and wiring loss.
- One-time request → separate interactive approval → authenticated amendment chain.
- Human-confirmed full re-baseline; agent-shell governance commands denied.
- Capability-first handling of unknown/MCP tools, plus symlink/path traversal resolution.
- Adversarial scripted cases for shell, Python, redirection, custom MCP, hook deletion,
  direct API mutation and self-rebaseline.
- Opt-in content-complete replay; verdict-only journals are explicitly not replayable.
- Wheel/sdist smoke CI and a prepared tag-driven workflow for PyPI, SBOM, provenance
  and release assets; first publication still requires the documented one-time setup.

## Evidence still required

### LiveAgentBench

Run a fixed, independently annotated task set multiple times with Claude Code and
Codex, with randomised arm assignment and paired runs with/without Northstar. Report:

- hard-constraint violations based on human labels, not Northstar findings;
- silent drift, false blocks, completion, escalation and detection latency;
- model/agent versions, prompts, seeds where available and raw content-complete traces;
- confidence intervals and per-task results, not only an aggregate percentage.

The current environment has neither agent executable nor credentials, so no live
numbers are fabricated in this repository. IntentDriftBench remains internal
regression evidence until this study is published. See the
[LiveAgentBench protocol](docs/live-agent-benchmark.md).

### Independent compiler evaluation

The 15 labelled descriptions in `tests/test_compiler.py` are a regression corpus, not
a held-out estimate. Build a larger English/Spanish set authored and labelled by
people who did not write the compiler rules, including paraphrases, negation,
conflicting constraints and adversarial phrasing. Publish the corpus and confusion
matrix before making an accuracy claim.

### External adoption

Pilot R1 on repositories not authored for Northstar. Track installation success,
false blocks, approval quality, bypasses, runtime overhead and abandonment. Issues,
stars and forks are not effectiveness evidence; reproducible external runs are.

## Security upgrades

### Authority daemon / OS-backed key

The current HMAC key is outside the working tree but accessible to an unrestricted
process running as the same OS user. A local daemon under a separate account, OS
keychain-backed signing, or hardware-backed approval would turn tamper evidence into a
stronger separation boundary. This is required before claiming malicious-agent
resistance without an external sandbox.

### Content-aware pre-write gates

Protected paths can be prevented before the write. API, dependency and graph drift are
usually detected after an edit lands. Parse proposed content for supported Edit/Write
shapes and reject a violating candidate before execution, while retaining post-state
checks as the backstop.

### Exact language parsers

Python is AST-based. JavaScript/TypeScript, Go, Rust and Java remain heuristic. Add
real grammars when external cases justify the dependency and maintenance cost.

## Deliberately deferred

- **LLM-as-judge blocking.** It would share the same degraded context and introduce
  arguable refusals. Any future semantic judge stays advisory.
- **Rollback.** Northstar reports drift; git owns recovery.
- **SaaS console.** Not before live-agent and external-adoption evidence exists.
