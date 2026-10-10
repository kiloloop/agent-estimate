---
name: estimate
description: Codex skill for running agent-estimate CLI commands (estimate, validate, calibrate).
---

# estimate (Codex)

Use this skill when a user asks to estimate AI-agent effort, compare agent time
with human time, validate an estimate against observed work, or recalibrate
local model factors.

The skill is command-first: execute the `agent-estimate` CLI and return its
output. Do not invent computed values.

## Intent Mapping

| User intent | Command |
| --- | --- |
| Estimate one task | `agent-estimate estimate "<task>"` |
| Estimate tasks from a file | `agent-estimate estimate --file <path>` |
| Estimate GitHub issues | `agent-estimate estimate --issues <nums> --repo <owner/name>` |
| Validate estimate vs actuals | `agent-estimate validate <observation.yaml>` |
| Recompute calibration summary | `agent-estimate calibrate` |

## Estimate Inputs

Accept exactly one input source:

- task description argument
- `--file <path>`
- `--issues <nums>` with `--repo <owner/name>`

If the input source is missing or ambiguous, ask for the missing piece.

## Estimate Flags

- `--config <path>` - custom agent fleet config.
- `--format markdown|json` - output format.
- `--review-mode none|standard|complex|3-round` - additive review tier:
  - `none`: +0m
  - `standard`: +15m
  - `complex`: +25m
  - `3-round`: +35m
- `--type coding|brainstorm|research|config|documentation|frontend|app_dev`.
- `--spec-clarity <0.3..1.3>`.
- `--warm-context <0.3..1.15>`.
- `--agent-fit <0.9..1.2>`.
- `--title <text>`.
- `--verbose`.

When `--type` is omitted, the CLI auto-detects the category. Research-grounded
brainstorms with citation, OSS, benchmark, source, or landscape signals route to
the research band instead of the flat brainstorm band.

## Type Guidance

- `coding`: default tiered PERT model for feature work, bug fixes, tests, and refactors.
- `brainstorm`: pure ideation and design exploration.
- `research`: audits, investigations, OSS comparisons, citation/source-grounded work.
- `config`: deploys, infra, CI/CD, runbooks, monitoring, and SRE changes.
- `documentation`: API docs, guides, README changes, changelogs.
- `frontend`: UI/page work. Content patches use 15/25/40; page builds use 40/60/90.
- `app_dev`: app shells and desktop/mobile builds. Uses a cold generic L-style prior; use modifiers for warm or highly specified work.

## Reliability Policy Model Keys

Current provenance-labeled local-policy keys include:

- `fable_5_1`, `opus_5_5`
- `gpt_6_astra`, `gpt_6_sol`, `gpt_6_luna`
- `gemini_3_1_pro`
- `sonnet_5_5`
- `haiku_4_5`

Earlier keys such as `opus_4_x`, `opus_4_7`, `opus_4_6`, `opus`,
`sonnet_4_6`, `gpt_5` through `gpt_5_5`, and `gemini_3_pro` remain accepted.

Warnings compare friction-adjusted work only. The shipped values are local
reliability policy (unmeasured), not published METR horizons. Duration priors
draw on 33 internal coding dispatches and 6 brainstorm dispatches; the report
states that the estimate pipeline applies no SQLite calibration-store feedback.

## Execution Rules

1. Execute commands from the repository root.
2. Prefer the installed `agent-estimate` binary.
3. If the binary is absent, use `python -m agent_estimate.cli.app`.
4. Capture stdout, stderr, and exit code.
5. If the command fails, return the error concisely and include the attempted command.
6. If the command succeeds, return the CLI output directly.
7. Treat `--format json` as a normal success path.

## Examples

```bash
agent-estimate estimate "Add login button with OAuth"
agent-estimate estimate "Audit dependencies for known CVEs" --type research
agent-estimate estimate "Build a landing page" --type frontend
agent-estimate estimate "Build an Electron app shell" --type app_dev --spec-clarity 0.3 --warm-context 0.3
agent-estimate estimate --file tasks.md
agent-estimate estimate --issues 1,2,3 --repo org/name
agent-estimate estimate --review-mode 3-round "Refactor auth module"
agent-estimate validate observation.yaml --db ~/.agent-estimate/calibration.db
agent-estimate calibrate --db ~/.agent-estimate/calibration.db
```

## Observation YAML Example

```yaml
task_type: frontend
estimated_minutes: 60.0
actual_work_minutes: 52.0
actual_total_minutes: 87.0
file_count: 4
line_count: 180
test_count: 3
execution_mode: single
review_mode: 3-round
review_overhead_minutes: 35.0
modifiers:
  spec_clarity: 1.0
  warm_context: 0.9
```

## Notes

- Requires `agent-estimate` installed: `pip install agent-estimate` or
  `pip install -e '.[dev]'` in this repo.
- Default config uses bundled `default_agents.yaml`.
- Estimates are generic priors. User-local `validate` and `calibrate` should tune
  them against local SQLite history over time.
