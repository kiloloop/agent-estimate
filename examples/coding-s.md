# Coding — Small (XS tier)

> Based on a real task from a production multi-agent workflow.

## Command

```bash
agent-estimate estimate "Fix pyproject.toml URLs after org rename"
```

## Output

Forecast basis: `expected-wall`; source: bundled task-category priors; n=0 calibration observations applied; as_of: unknown. Admission caps are not forecast or scoring inputs.

### Per-Task Estimates

| Task | Model | Tier | Agent | Base PERT (O/M/P) | Modifiers | Effective Duration | Human Equivalent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Fix pyproject.toml URLs after org rename** | coding | XS | Claude | 5m / 10m / 20m (E=10.8m) | spec 1.00 x warm 1.00 x fit 1.00 = 1.00 | 10.8m | 23m |

### Wave Plan

| Wave | Tasks | Duration | Agent Assignments (amortized review) |
| --- | --- | --- | --- |
| 0 | Fix pyproject.toml URLs after org rename | 27.5m | Claude: Fix pyproject.toml URLs after org rename +15m review |

### Timeline Summary

| Metric | Value |
| --- | --- |
| Best case | 21.3m |
| Expected case | 27.5m |
| Worst case | 37.2m |
| Human-speed equivalent | 23m |
| Compression ratio | 0.84x |
| Review overhead (per-task, pre-amortization) | 15m |

### Review Overhead

Review is amortized per agent per wave: one review cycle covers all PRs from that
agent in the wave.  Per-task values below are the naive (pre-amortization) figures.

| Task | Review Overhead |
| --- | --- |
| Fix pyproject.toml URLs after org rename | 15m |
| **Total (naive)** | **15m** |

### Agent Load Summary

| Agent | Task Count | Total Work | Estimated Cost |
| --- | --- | --- | --- |
| Claude | 1 | 12.5m | $0.30 |
| Codex | 0 | 0m | $0.00 |

### Critical Path

**Fix pyproject.toml URLs after org rename**

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
| Agent | Claude | Codex |
| Duration | 27.5m expected | ~25m |
| Outcome | — | PR merged, 11 files updated |

The task was dispatched to Codex (better fit for mechanical find-and-replace across many files). Finished within the estimate window. XS tasks like this are the bread and butter of agent dispatch — low risk, predictable, and the 2x compression adds up across dozens of small tasks per week.

> The shipped duration priors draw on 33 internal coding dispatches; this report applies no local calibration observations.

## Key takeaway

XS work is 1.84x faster than the work-only human equivalent, but the fixed 15m review cycle makes the end-to-end ratio 0.84x. For self-merged work where you trust the agent, use `--review-mode none`: an executed none-mode run keeps effective duration at 10.8m, drops expected case to 12.5m, and reports the same 23m work-only human equivalent for 1.84x compression. Review is deliberately not multiplied into the human comparison.
