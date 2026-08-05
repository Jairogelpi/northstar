# LiveAgentBench protocol

This document defines the study required before Northstar makes a live-agent
effectiveness claim. It is a protocol, not a result.

## Study question

For the same coding task and agent version, does enabling Northstar reduce
independently labelled constraint violations without an unacceptable increase in
false blocks or reduction in task completion?

## Design

- At least 20 realistic tasks from repositories not created for Northstar.
- Each task has an objective, repository commit, setup script, test command and an
  independently authored list of hard constraints.
- Paired arms: the same agent/task runs with and without Northstar.
- At least 10 repetitions per task and arm. Randomise arm order.
- Pin agent/model version and record all sampling controls exposed by the host.
- Evaluators must not see the arm while labelling outcomes.
- A second evaluator adjudicates every violation and a random sample of clean runs.
- Publish raw run manifests, content-complete traces when licensing permits, labels,
  exclusions and analysis code.

## Required run record

```json
{
  "schema": 1,
  "run_id": "uuid",
  "task_id": "repository/task",
  "arm": "with_runtime",
  "agent": {"host": "claude-code", "version": "...", "model": "..."},
  "repository": {"url": "...", "commit": "..."},
  "started": "RFC3339",
  "finished": "RFC3339",
  "exit_code": 0,
  "completed": true,
  "trace": "relative/path/to/trace.jsonl",
  "labels": {
    "hard_constraint_violations": [],
    "false_blocks": [],
    "human_escalations": [],
    "exclusion_reason": null
  },
  "annotators": ["opaque-evaluator-id"]
}
```

## Trace requirements

Set `NORTHSTAR_CAPTURE_REPLAY=1` only in the benchmark environment. This records
content-complete full-tree snapshots in the sealed external journal and can include
sensitive source. Do not enable it casually.

`bench.from_journal()` accepts only those content-complete snapshots. It rejects
ordinary verdict-only journals because a finding identifier plus empty content cannot
reconstruct an action.

Record the agent's native event log as well. Northstar's journal alone cannot describe
actions that bypassed Northstar, and using only product-observed events would bias the
study toward the product.

## Metrics

Report paired differences and 95% confidence intervals for:

- hard-constraint violation rate;
- silent drift rate;
- false-block rate;
- task-completion rate;
- human-escalation rate;
- detection latency in agent steps;
- runtime overhead and total task duration.

Do not define ground truth from Northstar's own findings. Do not discard failed agent
runs unless the exclusion rule was fixed before the study. Publish per-task results so
one easy task cannot hide regressions elsewhere.
