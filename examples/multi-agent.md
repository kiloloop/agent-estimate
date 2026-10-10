# Multi-Agent Session

> Based on real tasks from a production multi-agent workflow.

## Input file

Create a file `tasks.txt` with one task per line:

```
Implement add-agent CLI command with SPEC.md generation
Add known_debt.md as standard protocol memory file
Write quickstart guide with protocol comparison table
```

## Command

```bash
agent-estimate estimate --file tasks.txt
```

## Output

Forecast basis: `expected-wall`; source: bundled task-category priors; n=0 calibration observations applied; as_of: unknown. Admission caps are not forecast or scoring inputs.

### Per-Task Estimates

| Task | Model | Tier | Agent | Base PERT (O/M/P) | Modifiers | Effective Duration | Human Equivalent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Implement add-agent CLI command with SPEC.md generation** | coding | M | Claude | 25m / 50m / 90m (E=52.5m) | spec 1.00 x warm 1.00 x fit 1.00 = 1.00 | 52.5m | 176.1m |
| Add known_debt.md as standard protocol memory file | coding | M | Codex | 25m / 50m / 90m (E=52.5m) | spec 1.00 x warm 1.00 x fit 1.00 = 1.00 | 52.5m | 148.5m |
| Write quickstart guide with protocol comparison table | coding | M | Gemini | 25m / 50m / 90m (E=52.5m) | spec 1.00 x warm 1.00 x fit 1.00 = 1.00 | 52.5m | 148.5m |

### Wave Plan

| Wave | Tasks | Duration | Agent Assignments (amortized review) |
| --- | --- | --- | --- |
| 0 | Implement add-agent CLI command with SPEC.md generation, Add known_debt.md as standard protocol memory file, Write quickstart guide with protocol comparison table | 75.4m | Claude: Implement add-agent CLI command with SPEC.md generation +15m review; Codex: Add known_debt.md as standard protocol memory file +15m review; Gemini: Write quickstart guide with protocol comparison table +15m review |

### Timeline Summary

| Metric | Value |
| --- | --- |
| Best case | 44.7m |
| Expected case | 75.4m |
| Worst case | 117.2m |
| Human-speed equivalent | 473.1m |
| Compression ratio | 6.28x |
| Review overhead (per-task, pre-amortization) | 45m |

### Review Overhead

Review is amortized per agent per wave: one review cycle covers all PRs from that
agent in the wave.  Per-task values below are the naive (pre-amortization) figures.

| Task | Review Overhead |
| --- | --- |
| Implement add-agent CLI command with SPEC.md generation | 15m |
| Add known_debt.md as standard protocol memory file | 15m |
| Write quickstart guide with protocol comparison table | 15m |
| **Total (naive)** | **45m** |

### Agent Load Summary

| Agent | Task Count | Total Work | Estimated Cost |
| --- | --- | --- | --- |
| Claude | 1 | 60.4m | $1.45 |
| Codex | 1 | 60.4m | $0.97 |
| Gemini | 1 | 60.4m | $1.09 |

### Critical Path

**Implement add-agent CLI command with SPEC.md generation**

### Assumptions

- CLI task descriptions carry no dependency edges; scheduling assumes independence.
- Calibration store: n=0 observations applied; the estimate pipeline does not consume calibration feedback.
- Bundled-prior thinking-level baseline: Claude Code high and Codex extra-high.
- Human equivalent covers agent work only; human review is reported separately.
- Cost is a heuristic that assumes one agent turn per 5 minutes of work.

### Tier Corrections

No tier corrections.

### Reliability Horizon Warnings

- **Add known_debt.md as standard protocol memory file**: Work estimate (60.4m) exceeds gpt_6_sol local reliability policy (unmeasured) (60m). Consider splitting the task.
- **Write quickstart guide with protocol comparison table**: Work estimate (60.4m) exceeds gemini_3_1_pro local reliability policy (unmeasured) (45m). Consider splitting the task.


## What actually happened

These three tasks were dispatched to real agents in a production multi-agent workflow:

| Task | Agent | Estimated | Actual | Review | Outcome |
| --- | --- | --- | --- | --- | --- |
| add-agent CLI | Claude | 75.4m | ~90m | 2 rounds (R2 LGTM) | Merged |
| Protocol memory file | Codex | 75.4m | ~20m | Clean merge | Merged |
| Quickstart + README | Claude | 75.4m | ~45m | 2 rounds | Merged |

**Wall clock**: All three ran in parallel. Total elapsed ~90m (bounded by the slowest task). Work-only human equivalent: ~473m (~7.9 hours); review is accounted for separately.

**Actual compression**: 5.3x (90m wall clock / 473m work-only human equivalent). The estimate predicted 6.3x; one task (add-agent CLI) needed an extra review round that pushed it past the expected case.

The local-policy warnings were useful: Codex's task landed well under the configured 60m limit (20m actual), while the warning flagged that the Gemini-assigned task was near its 45m planning limit. These values are unmeasured policy guardrails, not published METR horizons.

## Key takeaway

Multi-agent sessions are where `agent-estimate` delivers the most value. Three agents working in parallel produce **6.3x compression** — ~75 minutes wall clock vs ~7.9 hours of work-only human equivalent. The wave planner automatically assigns tasks to agents, schedules them in parallel, and flags reliability risks via provenance-labeled local policy. You see the heuristic cost ($3.51), parallelism benefit, and risk before committing compute.
