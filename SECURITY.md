# Security

## Reporting

Open a [private security advisory](https://github.com/Jairogelpi/northstar/security/advisories/new).
Please do not open a public issue for a vulnerability.

## Threat model

Northstar's adversary is **a capable coding agent that is not trying to be
malicious** — one that will take the shortest path to a green result, including
paths you did not intend. It is not designed to stop a determined human attacker
with shell access, and it does not claim to.

### What it defends against

| Attack | Defence |
|---|---|
| Editing tests so the change looks correct | `protected_paths`, blocked at the pre-tool gate before the write lands |
| Editing the oracle or contract | `.northstar/**` is protected in code, not in config — the contract cannot disable it |
| Signing its own exception | `northstar amend` from an agent shell is denied; only a human signs |
| Forgetting a constraint after compaction | The contract is re-read from disk per call; the hook process holds no memory |
| Breaking an invariant then "restoring" it | Every check compares against the baseline, so violations are monotone |
| A subagent dropping the parent's constraints | Subagents hit the same on-disk contract; nothing is passed through a prompt |

### What it does not defend against

Stated plainly, because a guardrail whose limits are unclear is worse than none:

- **An agent with unrestricted shell that bypasses the hook.** If the agent can run
  commands the gate never sees — a detached process, a different working directory, a
  shell the adapter is not wired into — nothing here applies. Northstar is a
  correctness guardrail, not a sandbox. Run it inside one.
- **Editing `.northstar/` outside the agent's tool calls.** The gate blocks tool
  writes. It does not make the directory read-only at the filesystem level. Commit
  the contract and oracle to git so tampering shows up in a diff.
- **Languages with no extractor.** Their API surface is `UNKNOWN`, not protected.
- **Pattern-based extractors** (JS/TS, Go, Rust, Java) can miss exotic declarations.
  Findings from them say `heuristic extractor` for exactly this reason.
- **Anything the contract does not name.** Northstar is a deny-list. Silence is
  permission, by design.

### Deliberate design decisions

- `UNKNOWN` is a verdict, never an implicit pass. A file that cannot be parsed is
  reported as uncovered, because claiming coverage we do not have is the failure mode
  that makes a guardrail worse than nothing.
- Amendments re-baseline only what they name. One signature is never a general
  amnesty.
- Refusals of an *action* (a forbidden command, an attempted self-amend) cannot be
  granted by any signature, and the block message does not offer a grant for them —
  offering one would invite a retry of what was just refused.
