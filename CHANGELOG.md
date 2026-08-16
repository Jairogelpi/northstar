# Changelog

## 0.2.0 - Unreleased

### Integrity boundary

- Runtime authority moved outside the working tree, with readable in-repository mirrors.
- HMAC-sealed contract, oracle, journal, project metadata, and mirror digests.
- Fail-closed hooks and CLI checks for missing, corrupt, or mismatched authority state.
- Structural integrity checks for Claude Code and Codex wiring.
- Native Codex `PreToolUse`/`PostToolUse` wiring via `.codex/hooks.json`; removes the
  project-local `notify` integration that current Codex ignores.
- One-time approval requests and Ed25519-signed interactive approval; the private key
  is passphrase-encrypted and `--signed-by` was removed.
- Human-confirmed full re-baselines; agent-shell governance mutations are denied.
- Explicit v0.1 migration that re-authenticates reviewed legacy amendments.

### Adversarial evidence

- Capability-first handling for unknown and MCP tools.
- Shell, Python, redirection, custom MCP, hook deletion, direct API, and rebaseline attacks in IntentDriftBench.
- Content-complete journal replay is opt-in; legacy verdict-only journals are no longer misrepresented as replays.
- Pytest behavioural capture now uses the active interpreter and works with current pytest releases.
- Executable LiveAgentBench pipeline for real Claude Code/Codex runs: strict manifests,
  pinned version checks, paired randomisation, isolated clones, complete-tree/native
  traces, arm-blinded packets, independent annotations, observed-hook validation, and
  aggregate/per-task bootstrap analysis. No live result is claimed by the harness.

### Distribution

- Wheel/sdist build and clean-install smoke tests in CI.
- Tag-driven PyPI trusted publishing, GitHub release assets, SBOM, and build provenance workflow.

### Onboarding and evidence

- Disposable `northstar demo` exercises freeze, enforcement, drift detection and a
  scoped approval without modifying user files.
- Read-only `northstar doctor` verifies the runtime, sealed authority, mirrors, agent
  wiring, current verdict, observed hook activity and agent executables.
- `northstar init --dry-run` previews the exact contract. Task-derived initialization
  now stops on uncompiled sentences unless the human explicitly accepts them.
- Authenticated `northstar uninstall` removes only managed Claude/Codex wiring and
  preserves unrelated settings and instruction text.
- LiveAgentBench preflight checks exact agent versions and repository commits before
  token-consuming runs; the Markdown report gate accepts only independent annotation
  ground truth.
- Added a five-minute quickstart, explicit product positioning, a public evidence
  ladder and claim publication policy.

## 0.1.0 - 2026-08-04

- Initial deterministic contract, oracle, checks, adapters, compiler, receipt, and scripted benchmark.
