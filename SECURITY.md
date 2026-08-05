# Security

## Reporting

Open a [private security advisory](https://github.com/Jairogelpi/northstar/security/advisories/new).
Please do not open a public issue for a vulnerability.

## Security claim

Northstar is a deterministic correctness guardrail with a **tamper-evident,
fail-closed integrity boundary**. It is not an OS sandbox and does not claim to stop
a malicious process with unrestricted access as the same operating-system user.

That distinction is deliberate:

- the working tree is untrusted;
- the canonical contract, oracle, journal and metadata live in the OS data directory;
- an HMAC manifest binds the canonical artifacts and their readable in-tree mirrors;
- Ed25519-signed amendments bind a one-time request id, grants, reason, signer and
  contract version; the private key is stored encrypted by a human passphrase and the
  runtime verifies with the public key;
- missing or corrupt authority, mirrors or agent wiring returns a blocking
  `INTEGRITY_FAILURE`;
- a process with unrestricted same-user filesystem access can ultimately reach the
  HMAC key. Use a sandbox, separate service account or future authority daemon when
  defending against that process is required.

HMAC is used to distinguish normal runtime writes, accidental corruption and direct
working-tree tampering. Ed25519 authenticates the amendment chain. The recorded OS
username is attribution; possession of the approval passphrase is the signing
authority.

## Threat model

The primary adversary is a capable coding agent pursuing a green result through any
tool exposed by its host, including shell and custom MCP tools. The active Claude Code
hook is expected to see each supported tool call. Claude Code and current Codex both
provide pre-action blocking and post-action checks; Codex project hooks must first be
reviewed and trusted by the human through `/hooks`.

| Attack | R1 behaviour |
|---|---|
| Edit/delete `.northstar/**` with Edit/Write/apply_patch | Pre-tool `DENY`; mirror verification also fails closed |
| `rm`, `sed -i`, `mv`, redirection or `python -c` against Northstar state | Obvious references are denied before shell execution; seal verification detects a miss afterward |
| Delete Claude/Codex wiring | Wiring files are hard-protected and structurally verified; current post-hook fails closed if deletion lands |
| Unknown/custom MCP tool | Blocking `REQUIRE_APPROVAL` until classified as `read_only` or `mutating`; protected targets still deny |
| Path traversal or symlink into `.northstar` | Target is resolved before protected-path matching |
| Delete/corrupt contract or oracle | External canonical copy remains; any missing/mismatched mirror is `INTEGRITY_FAILURE` |
| Replace canonical artifact | Manifest digest/HMAC fails |
| Rewrite an amendment and reseal the bundle | Ed25519 verification fails |
| Invoke `northstar approve`, `freeze`, `init`, `migrate` or `install` from agent shell | Governance action is denied by the gate; trusted mutations also require the approval passphrase |
| Invoke `northstar amend` | Creates an untrusted request only; the live contract is unchanged |
| Reuse an approval | Consumed request ids are rejected |
| Malformed/missing hook payload in governed project | Unknown capability is blocking, not fail-open |

## Residual risks

- **Same-user unrestricted shell.** Shell is Turing-complete; no command parser can
  prove an arbitrary program is read-only. An agent that can bypass hooks and access
  the authority directory as the same user can read the integrity key, replace state,
  or remove both marker and authority. Northstar detects ordinary and tested paths;
  it is not the isolation boundary. Run the agent in a sandbox that cannot write the
  authority directory when malicious-agent resistance matters.
- **Detached processes and unobserved tools.** If the agent host does not invoke the
  hook, only the next explicit Northstar check can detect drift. Codex documents that
  some specialised tool paths may opt out of the default hook path.
- **Codex trust is user-local.** Northstar verifies `.codex/hooks.json`, but Codex
  skips non-managed hooks until the human trusts their exact definition through
  `/hooks`. Disabling the hooks feature or withholding trust disables enforcement;
  the project cannot inspect that user-local decision.
- **Hook self-repair is not atomic rollback.** A post-hook can report deleted wiring,
  but the mutating command has already executed. Human repair is required.
- **Languages without exact extractors.** Unsupported surfaces are `UNKNOWN`;
  heuristic extractors can miss exotic declarations.
- **Anything the contract does not name.** Northstar is a deny-list. Silence is
  permission except for the hard integrity boundary and unclassified tool capability.
- **Approval terminal provenance.** The encrypted signing key requires a passphrase
  read from an interactive terminal and removes arbitrary `--signed-by`. It is still
  not hardware-backed identity; a future authority daemon or OS keychain integration
  can strengthen key custody.

## Operational guidance

1. Keep the agent inside a filesystem/network sandbox appropriate to its threat level.
2. Do not give the agent write access to the configured Northstar data directory when
   treating it as adversarial.
3. Review every request's exact grants and reason before typing the TTY challenge.
4. Treat `INTEGRITY_FAILURE` as an incident: stop the run, inspect the tree and
   authority, then repair wiring or deliberately re-initialise.
5. Commit the readable `.northstar/` mirrors for review, while remembering that the
   runtime authority is external.
