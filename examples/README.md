# Examples

Real input/output examples from production agent dispatches. Every command below is copy-paste runnable after `pip install agent-estimate`. Each example includes a "What actually happened" section showing the real dispatch outcome.

| Example | Task type | Tier | Compression | File |
|---------|-----------|------|-------------|------|
| Fix pyproject URLs after org rename | Coding | XS | 0.84x | [coding-s.md](./coding-s.md) |
| Implement CLI command with code generation | Coding | M | 2.34x | [coding-m.md](./coding-m.md) |
| Audit cloud infrastructure providers | Research | S | 1.49x | [research.md](./research.md) |
| Write quickstart guide + README | Documentation | S | 2.45x | [documentation.md](./documentation.md) |
| 2-agent parallel session (3 features) | Multi-agent | M×3 | 6.28x | [multi-agent.md](./multi-agent.md) |

## How to read the output

- **Tier** — task size: XS (~10m), S (~24m), M (~52m), L (~100m), XL (~195m)
- **PERT (O/M/P)** — optimistic / most-likely / pessimistic estimates, weighted to expected
- **Human Equivalent** — how long the agent's work would take a human developer; review stays separate
- **Compression ratio** — human time / agent time. Higher = more agent leverage.
- **Wave** — parallel scheduling group. Tasks in the same wave run concurrently.
- **Reliability warning** — fires when friction-adjusted work exceeds a provenance-labeled policy limit
- **What actually happened** — real dispatch data showing estimate vs actual outcome

## Calibration data

These examples are drawn from a production multi-agent fleet (Claude, Codex) running real development tasks. The shipped priors are informed by internal dispatches, but the estimate pipeline does not yet apply the local calibration store:

| Metric | Value |
|--------|-------|
| Coding dispatches informing priors | 33 |
| Brainstorm dispatches informing priors | 6 |
| SQLite calibration-store observations applied | 0 |
| Task types covered | Coding, research, documentation, brainstorm, config |
| Thinking-level baseline | Claude Code High, Codex Extra High |

Use `agent-estimate validate` to record your own dispatch outcomes. The current estimate pipeline reports bundled priors until a future calibrated snapshot is wired into forecasting.
