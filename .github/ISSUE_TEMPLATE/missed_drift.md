---
name: Missed drift
about: An agent broke a constraint and Northstar said nothing
title: "missed drift: "
labels: missed-drift
---

<!--
Silent drift is the failure this project exists to prevent. If a violation
reached the final tree without a word, that is a defect regardless of cause.
-->

## What the agent did

## What the contract said

```yaml
# .northstar/contract.yaml
```

## What Northstar reported

```
$ northstar check
<paste the output>
```

## Was it reported as UNKNOWN?

<!--
UNKNOWN means "not covered", which is a known gap rather than a silent pass --
but if a constraint you expected to be enforceable came back UNKNOWN, say so.
That is still worth fixing.
-->

## Environment

- northstar version:
- Python version:
- OS:
- Agent (Claude Code / Codex / CLI only):
