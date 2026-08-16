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
- Disposable product demo, contract preview, read-only diagnostics and authenticated
  uninstall with preservation of unrelated agent settings.
- Live study preflight and publication-safe independent-evidence report renderer.

## Evidence still required

### LiveAgentBench execution

The executable harness is implemented: strict study manifests, pinned version checks,
fresh commit-pinned clones, deterministic paired randomisation, complete tree/native
trace capture, blinded outcome packets, separate outcome/process annotations, observed
hook validation, and aggregate/per-task paired bootstrap analysis.

The remaining work is to run a fixed, independently annotated task set multiple times
with Claude Code and Codex and publish:

- hard-constraint violations based on human labels, not Northstar findings;
- silent drift, false blocks, completion, escalation and detection latency;
- model/agent versions, prompts, seeds where available and raw content-complete traces;
- confidence intervals and per-task results, not only an aggregate percentage.

No live numbers are fabricated in this repository. IntentDriftBench remains internal
regression evidence until the external study is published. See
[LiveAgentBench](docs/live-agent-benchmark.md).

The runner environment must contain the pinned Claude Code/Codex executables and their
required credentials. Outcome annotators must be independent of Northstar findings;
these are operational dependencies, not implementation work that can be replaced by
synthetic labels.

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

### Distribution and onboarding follow-through

- Configure the one-time PyPI Trusted Publisher and protected GitHub `pypi`
  environment, publish `0.2.0`, and run the documented public-index smoke test.
- Record first-install completion and `doctor` warnings from opt-in pilots.
- Add exact parsers or integration support only when external usage identifies the
  highest-value language or agent host.

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
