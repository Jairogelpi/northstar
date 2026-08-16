# Evidence and claim policy

Northstar's central claim must be easier to audit than its marketing. This file is the
public claim ledger: what has been measured, what the measurement supports, and what
is still missing.

## Evidence ladder

| Level | Required evidence | Allowed claim |
|---|---|---|
| 0 — mechanism | Source, threat model and deterministic examples | “Northstar is designed to detect these invariant changes.” |
| 1 — implementation | Automated tests across supported platforms | “The documented checks behave as specified in the tested cases.” |
| 2 — authored evaluation | Reproducible product-authored paired corpus | “Northstar achieved X in this scripted corpus.” |
| 3 — independent live evaluation | Real agents, preregistered tasks, blinded independent labels, raw artifacts and intervals | “Under these published study conditions, Northstar changed X by Y.” |
| 4 — external adoption | Third-party repositories and reported operational outcomes | “External teams observed X in these deployments.” |

No result inherits a stronger level merely because it is large, favourable or
repeated by the product author.

## Current ledger

### Automated implementation evidence — level 1

- CI enforces at least 95% branch coverage.
- The test matrix covers Python 3.11–3.13 on Linux and Windows, plus Python 3.12 on
  macOS.
- Package CI builds both wheel and source distribution, validates metadata, installs
  the wheel in a clean environment and runs the CLI demo.
- Adversarial tests exercise direct writes, shell commands, Python execution,
  redirection, symlinks, unknown MCP tools, hook deletion, approval reuse and direct
  governance API attempts.

This supports implementation claims only. It does not estimate agent behaviour.

### IntentDriftBench — level 2

The built-in corpus contains 24 scripted paired trajectories: 22 authored adversarial
cases and two clean controls. Every commit reruns it with and without enforcement.

Verified on 2026-08-16 with Python 3.12.13:

| Metric | Without runtime | With runtime |
|---|---:|---:|
| Hard-constraint violation rate | 91.7% | 20.8% |
| Silent drift rate | 91.7% | 0.0% |
| False block rate | 0.0% | 0.0% |
| Human escalation rate | 0.0% | 4.2% |
| Task completion rate | 100.0% | 100.0% |
| Detection latency | 0.0 steps | 0.0 steps |

Reproduce it with:

```bash
northstar bench --json
```

The corpus is written by the product author and uses scripted actions rather than live
agents. These values are regression evidence, not an independent effectiveness rate.
Hard-constraint violations remain in the protected arm because some state checks
detect an edit after it lands; detection is not rollback.

### LiveAgentBench — infrastructure ready, level 3 result pending

The repository includes a strict manifest, exact CLI version checks, commit-pinned
fresh clones, paired randomisation, blinded packets, separate outcome/process labels,
observed-hook validation, paired bootstrap intervals and a publication-safe Markdown
reporter.

Before spending agent tokens, verify the environment:

```bash
northstar live-bench preflight study.yml --check-repositories
```

The reporter accepts only `ground_truth: independent_annotations`. This prevents an
internal Northstar finding from being relabelled as an externally judged outcome.

There is currently **no published level 3 result**. A valid result requires real
Claude Code and/or Codex executions plus annotators who did not author the product
findings. Until those artifacts exist, the allowed claim is “evaluation ready.”

### External adoption — level 4 pending

There is currently no published third-party pilot with installation success, false
blocks, abandonment, approval quality and runtime overhead. GitHub stars, downloads
and testimonials are useful adoption signals but are not effectiveness evidence.

## Publication gate

A live result may appear in the README only when all of the following are published
together:

- the immutable study manifest and its hash;
- task repository URLs and exact commits;
- agent, model and complete version-command output;
- preregistered paired plan and declared exclusions;
- blinded packets, frozen outcome labels and process labels;
- the private map released after annotation;
- raw native traces and Northstar journals where licences permit;
- aggregate and per-task metrics with paired 95% intervals;
- failures, timeouts, missing observations and study limitations.

The workflow and schemas are documented in
[LiveAgentBench](docs/live-agent-benchmark.md).

## Reporting a contradictory result

Open an issue containing the smallest reproducible contract, baseline, action and
unexpected verdict. False blocks are treated as first-class evidence failures. For a
security vulnerability, use a private advisory as described in
[SECURITY.md](SECURITY.md).

