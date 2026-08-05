# Changelog

## 0.2.0 - Unreleased

### Integrity boundary

- Runtime authority moved outside the working tree, with readable in-repository mirrors.
- HMAC-sealed contract, oracle, journal, project metadata, and mirror digests.
- Fail-closed hooks and CLI checks for missing, corrupt, or mismatched authority state.
- Structural integrity checks for Claude Code and Codex wiring.
- One-time approval requests and Ed25519-signed interactive approval; the private key
  is passphrase-encrypted and `--signed-by` was removed.
- Human-confirmed full re-baselines; agent-shell governance mutations are denied.
- Explicit v0.1 migration that re-authenticates reviewed legacy amendments.

### Adversarial evidence

- Capability-first handling for unknown and MCP tools.
- Shell, Python, redirection, custom MCP, hook deletion, direct API, and rebaseline attacks in IntentDriftBench.
- Content-complete journal replay is opt-in; legacy verdict-only journals are no longer misrepresented as replays.
- Pytest behavioural capture now uses the active interpreter and works with current pytest releases.

### Distribution

- Wheel/sdist build and clean-install smoke tests in CI.
- Tag-driven PyPI trusted publishing, GitHub release assets, SBOM, and build provenance workflow.

## 0.1.0 - 2026-08-04

- Initial deterministic contract, oracle, checks, adapters, compiler, receipt, and scripted benchmark.
