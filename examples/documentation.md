# Documentation

> Based on a real task from a production multi-agent workflow.

## Command

```bash
agent-estimate estimate \
  "Write quickstart guide and README with protocol comparison table" \
  --type documentation
```

## Output

Forecast basis: `expected-wall`; source: bundled task-category priors; n=0 calibration observations applied; as_of: unknown. Admission caps are not forecast or scoring inputs.

### Per-Task Estimates

| Task | Model | Tier | Agent | Base PERT (O/M/P) | Modifiers | Effective Duration | Human Equivalent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Write quickstart guide and README with protocol comparison …** | documentation | S | Claude | 10m / 25m / 45m (E=25.8m) | spec 1.00 x warm 1.00 x fit 1.00 = 1.00 | 25.8m | 109.6m |

### Wave Plan

| Wave | Tasks | Duration | Agent Assignments (amortized review) |
| --- | --- | --- | --- |
| 0 | Write quickstart guide and README with protocol comparison … | 44.7m | Claude: Write quickstart guide and README with protocol comparison … +15m review |

### Timeline Summary

| Metric | Value |
| --- | --- |
| Best case | 27.4m |
| Expected case | 44.7m |
| Worst case | 65.7m |
| Human-speed equivalent | 109.6m |
| Compression ratio | 2.45x |
| Review overhead (per-task, pre-amortization) | 15m |

### Review Overhead

Review is amortized per agent per wave: one review cycle covers all PRs from that
agent in the wave.  Per-task values below are the naive (pre-amortization) figures.

| Task | Review Overhead |
| --- | --- |
| Write quickstart guide and README with protocol comparison … | 15m |
| **Total (naive)** | **15m** |

### Agent Load Summary

| Agent | Task Count | Total Work | Estimated Cost |
| --- | --- | --- | --- |
| Claude | 1 | 29.7m | $0.71 |
| Codex | 0 | 0m | $0.00 |

### Critical Path

**Write quickstart guide and README with protocol comparison …**

### Assumptions

- CLI task descriptions carry no dependency edges; scheduling assumes independence.
- Calibration store: n=0 observations applied; the estimate pipeline does not consume calibration feedback.
- Bundled-prior thinking-level baseline: Claude Code high and Codex extra-high.
- Human equivalent covers agent work only; human review is reported separately.
- Cost is a heuristic that assumes one agent turn per 5 minutes of work.

### Tier Corrections

No tier corrections.

### Reliability Horizon Warnings

No reliability horizon warnings.


## What actually happened

| Metric | Estimated | Actual |
| --- | --- | --- |
| Agent | Claude | Claude |
| Duration | 44.7m expected | ~45m |
| Review | 1 round assumed | 2 rounds (Codex R1 feedback → R2 LGTM) |
| Outcome | — | PR merged — README, quickstart, protocol comparison table |
| Quality | — | Q4 (strong) |

Almost exactly on estimate. Documentation tasks are the most predictable category because the scope is well-defined (write X about Y) and agents produce structured output consistently. The two review rounds fit within the estimate window because doc reviews are faster than code reviews.

## Key takeaway

Documentation has the highest human-multiplier range (3.0-6.0x) because humans find technical writing tedious and slow. Agents produce consistent, structured output quickly. The 2.45x end-to-end compression keeps the 15m review cycle separate — without review (`--review-mode none`), an executed none-mode run reports 25.8m effective duration, 29.7m expected case, the same 109.6m work-only human equivalent, and 3.69x compression.
