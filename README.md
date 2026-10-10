# agent-estimate

[![PyPI Version](https://img.shields.io/pypi/v/agent-estimate)](https://pypi.org/project/agent-estimate/)
[![Python Versions](https://img.shields.io/pypi/pyversions/agent-estimate)](https://pypi.org/project/agent-estimate/)
[![License](https://img.shields.io/pypi/l/agent-estimate)](https://github.com/kiloloop/agent-estimate/blob/main/LICENSE)
[![CI](https://github.com/kiloloop/agent-estimate/actions/workflows/ci.yml/badge.svg)](https://github.com/kiloloop/agent-estimate/actions/workflows/ci.yml)

**Know before you build.**

PERT estimates for AI-agent tasks — how long, which model's reliable enough, and the human-equivalent cost. In one command.

**[Website](https://kiloloop.com/agent-estimate/)** · [Compare](https://kiloloop.com/agent-estimate/compare/) · [PyPI](https://pypi.org/project/agent-estimate/)

## Why

AI agents can write the code — but *how long will the task actually take?* Manual estimation is slow and biased toward optimism; no estimate means scope creep and missed deadlines. The gap between "agents can do it" and "we know when it'll be done" is where projects break down.

`agent-estimate` closes that gap in one command: a three-point PERT timeline built from priors drawn from 33 internal coding dispatches and 6 brainstorm dispatches, plus a human-speed comparison so you see the compression before you spend the compute. It sizes the task, picks a tier, routes it to a model, and flags when the work exceeds that model's configured reliability policy — forecasts in seconds, not meetings.

Multi-model matters because the models aren't interchangeable. A measured p80 horizon is the human-expert task duration at which a model is estimated to succeed 80% of the time. The shipped limits below are instead provenance-labeled local policy (unmeasured), because current models such as Opus 5.5 and GPT-6 Astra do not have matching published measurements. agent-estimate models the whole fleet, not a single agent — so the number reflects who actually runs the work.

## Quick Start

> First estimate: 30 seconds to install. Every one after: instant.

### With your agent (recommended)

Paste this into your Claude Code or Codex session:

~~~
Install the agent-estimate plugin (https://github.com/kiloloop/agent-estimate) and
estimate this task for me: "Implement OAuth 2.0 flow (Google + GitHub)". Tell me the
expected time, the human-speed equivalent, and the compression ratio.
~~~

Your agent installs the tool, runs the estimate, and reads back the numbers. Nothing to memorize — describe the task in plain English and let the agent translate to flags.

For a whole backlog:

~~~
Estimate every open issue in this repo with agent-estimate, group them into parallel
waves, and tell me the total wall-clock time for a 3-agent fleet versus doing them
sequentially myself.
~~~

### Manual

```bash
pip install agent-estimate
agent-estimate estimate "your task description here"
```

No config required — sensible defaults for a 3-agent fleet (Claude, Codex, Gemini). Point it at a file or GitHub issues when you're ready:

```bash
agent-estimate estimate --file tasks.txt
agent-estimate estimate --repo myorg/myrepo --issues 11,12,14
agent-estimate session --agents 3 --rounds 2 --type review
```

### v0.8 forecast contract

The v0.8 source introduces a versioned request for callers that already know the
task and executor. These features are not in the published v0.7.5 package; use an
installation from this checkout until v0.8 is released. Bare task descriptions,
text files, and GitHub issue input remain available.

| Contract | Owns |
|----------|------|
| `TaskSpec` | Task facts, required capabilities, dependencies, and independent scope estimates |
| `ExecutionProfile` | Runtime and model identity, configuration provenance, context, modifiers, and review plan |
| `AdmissionEnvelope` | Declared minute/file caps and optional replayable cap calculations |
| `ForecastRecord` | Expected wall minutes, expected files, additive review minutes, and forecast provenance |

The [typed contracts](src/agent_estimate/contract/schema.py) are frozen, reject
unknown fields and non-finite numbers, and require explicit schema versions.
Model identity must be either a supplied identifier or an `unknown_reason`;
selecting an agent does not establish its serving model. IDs are caller supplied.
The library also exposes `OutcomeObservation` with nullable future actuals slots;
it does not ingest actuals or persist binding receipts.

From the repository root, with the matching package installed, run the complete
checked-in [request example](examples/estimate-request.yaml):

```bash
agent-estimate estimate --spec examples/estimate-request.yaml --format json
```

The JSON includes `"schema_version": "agent-estimate/report/v1"`, assigns
`Add input validation` to `Codex`, and reports `forecast.basis: expected-wall`.
The example's 90-minute admission cap does not determine its expected minutes.
`--spec` emits the versioned report; library callers can construct the separate
`ForecastRecord` with `forecast_from_report` in `agent_estimate.contract.duration`.

`--spec` accepts one full `EstimateRequest` containing `task_spec`,
`execution_profile`, and `admission`, plus an optional `token_prior`. It can be
combined with `--config`, `--format`, `--compact`, and `--title`. Put task facts,
scope, and modifiers in the request; alternate input sources and history flags
cannot be combined with `--spec`. Conflicts and invalid fields exit 2. It does
not read ambient `data.json` history.
The named agent must exist in the selected fleet and satisfy every required
capability. `config_profile` records provenance; `--config` selects the actual
fleet file. This single-task CLI path requires empty `dependency_task_ids` and
`execution_profile.estimate_multiplier: 1.0`; use the configured agent's
`estimate_multiplier` for [profile adjustments](#agent-fleet).

Context identity belongs to `execution_profile.context.context_key`. An explicit
`modifiers.warm_context` takes precedence: pairing it with
`context.implicit_co_dispatch: true` is rejected to prevent double counting.
Review intent is additive: supported plans are no review, one or two standard or
complex rounds, or three standard rounds. Unsupported plans exit 2.

**Expected values and caps.** Reports label duration `expected-wall`, with
`source` and `as_of` provenance (an unknown date stays null). Expected wall minutes
include work and additive review. Score matching actual wall minutes against
independent expected minutes, never `declared_cap_minutes` or a cap divided by
guessed headroom. File expectations likewise come from task scope, not file caps.
The `validate` command uses `actual_total_minutes` for `expected-wall` observations
and `actual_work_minutes` for `expected-work`; cap-only and cap-derived inputs are
rejected. Wall scoring is report-only in the current calibration store. Legacy
`estimated_minutes` means expected work only, and `calibrate --basis expected-work`
is an explicit attestation that every stored estimate is expected work rather
than a cap; inspect the rows before using it. Without that attestation,
`calibrate` exits 2 with guidance on stderr.

**Token honesty.** Typed forecasts default to `tokens.basis: unavailable`, with
null `expected_tokens_total`, `expected_tokens_output` and
`expected_tokens_cache_read`. Total means processed tokens including cache carry;
output and cache-read tokens are separate counts included in total. A
caller-supplied prior uses `basis: local-policy`, a source, date, population,
and a mandatory population mismatch warning. An absent count stays unavailable;
zero is a supplied count. A caller can also pass its own observed tokens per
closed leg with `--token-observations`; the request's task-type × execution-profile
segment then reports `basis: measured` at five or more legs, as a shrunk
log-median that names the segment, its `n` and the observation window. JSON
includes `forecast.tokens` when a prior or observations are supplied; Markdown
then shows the slots and their provenance. There are **no packaged token priors,
numeric rates or coefficients**. See the
[explicitly uncalibrated rate-shape example](docs/token-forecast-priors.md#rate-shape-example-only--not-calibrated)
and the [measured correction](docs/token-forecast-priors.md#measured-correction).

**Subscription points (experimental).** `--meter-table` applies a caller-supplied,
dated meter table to the token forecast and reports `forecast.subscription` for
the request's assigned agent: expected points, the fraction of the reset window,
and their basis. The meter is selected by `execution_profile.model.id`. Points
stay `unavailable`, with the reason, without a token forecast, its cache-read
count, a model id or a meter for it. There are **no packaged meter numbers**.
See [subscription points](docs/subscription-points.md).

Upgrading configuration or JSON consumers? Read the
[v0.8 migration notes](docs/migration-v0.8.md) for the two removed surfaces.

## How It Works

agent-estimate produces three-point [PERT](https://en.wikipedia.org/wiki/Program_evaluation_and_review_technique) estimates from agent-work priors, not human-duration estimates:

- **Tier classification** — auto-sizes tasks XS→XL from complexity signals
- **PERT math** — optimistic / most-likely / pessimistic, weighted to an expected value
- **Human comparison** — a per-task-type multiplier, so you see the compression
- **Reliability policies** — warns when friction-adjusted work exceeds a provenance-labeled model limit
- **Wave planning** — schedules independent tasks in parallel across the fleet
- **Review overhead** — models review cycles as additive cost (`standard`, `complex`, `3-round`)
- **Modifiers** — `--spec-clarity`, `--warm-context`, `--agent-fit` tune the estimate

### Task types

| Type | Flag | Models |
|------|------|--------|
| Coding | (default) | Feature work, fixes, refactors |
| Research | `--type research` | Audits, investigations, analysis |
| Documentation | `--type documentation` | API docs, guides, changelogs |
| Brainstorm | `--type brainstorm` | Ideation, spikes, design exploration |
| Config/SRE | `--type config` | Deploys, infra, CI/CD |
| Frontend/UI | `--type frontend` | Content patches vs. component builds |
| App dev | `--type app_dev` | App shells, desktop/mobile builds |

### Reliability policy defaults

| Model | Work limit | Basis | As of |
|-------|------------|-------|-------|
| Fable 5.1 | 90 min | Local policy (unmeasured) | 2026-09-28 |
| Opus 5.5 | 90 min | Local policy (unmeasured) | 2026-09-28 |
| GPT-6 Astra | 90 min | Local policy (unmeasured) | 2026-09-28 |
| GPT-6 Sol | 60 min | Local policy (unmeasured) | 2026-09-28 |
| Gemini 3.1 Pro | 45 min | Local policy (unmeasured) | 2026-08-23 |
| Sonnet 5.5 | 30 min | Local policy (unmeasured) | 2026-09-28 |
| GPT-6 Luna | 15 min | Local policy (unmeasured) | 2026-09-28 |
| Haiku 4.5 | 15 min | Local policy (unmeasured) | 2026-08-23 |

Every row records `basis`, `source`, `source_version`, and `as_of` in `metr_thresholds.yaml`; the source of every default above is the agent-estimate default reliability policy (registry `v0.9-policy-1`). A row added in that registry carries the limit of the tier it fills, so no limit changed. The `claude`, `codex`, `production`, `sonnet`, and `haiku` aliases resolve to Opus 5.5, GPT-6 Astra, GPT-6 Sol, Sonnet 5.5, and Haiku 4.5, and a typed request's vendor model id (`claude-fable-5-1`, `gpt-6-sol`) resolves to its row. Earlier keys (`opus_4_x`, `opus_4_7`, `opus_4_6`, `opus`, `sonnet_4_6`, GPT-5 through GPT-5.5, Gemini 3 Pro) stay supported. The bundled thinking-level baseline is Claude Code high and Codex extra-high — shift with `--spec-clarity` and `--warm-context` for other setups.

## Examples

Real estimates from production use — including the misses.

**The tool, estimating its own docs.** We sized this v0.7.0 skill-and-README refresh at ~30 minutes. It took 28.

**An honest over-estimate.** We pre-registered a UI mockup build at ~95 minutes with no prior app-dev data. Two agents did it in parallel in 12 and 25 minutes — a 4–8x over-estimate. agent-estimate now ships an `app_dev` prior shaped by that result. The miss stays in the README because calibration means showing where you were wrong.

**Three tasks, three agents, in parallel** — what the tool prints, including the reliability-policy flags. Input is the three-task `tasks.txt` from [`examples/multi-agent.md`](./examples/multi-agent.md); the output below is captured from a real run, trimmed to the timeline and warnings (the full report — per-task PERT table, wave plan, assumptions, and agent loads — is in that example):

```text
$ agent-estimate estimate --file tasks.txt

## Timeline Summary

| Metric | Value |
| --- | --- |
| Best case | 44.7m |
| Expected case | 75.4m |
| Worst case | 117.2m |
| Human-speed equivalent | 473.1m |
| Compression ratio | 6.28x |
| Review overhead (per-task, pre-amortization) | 45m |

## Reliability Horizon Warnings

- **Add known_debt.md as standard protocol memory file**: Work estimate (60.4m) exceeds gpt_6_sol local reliability policy (unmeasured) (60m). Consider splitting the task.
- **Write quickstart guide with protocol comparison table**: Work estimate (60.4m) exceeds gemini_3_1_pro local reliability policy (unmeasured) (45m). Consider splitting the task.
```

~75 minutes wall-clock versus the work-only human equivalent, at an estimated $3.51 fleet cost — plus policy flags when assigned work exceeds a model's configured limit, so you split it or add a checkpoint before dispatching. Human review is modeled separately. The same three tasks were later run by real agents; the retro is in the example file. More in [`examples/`](./examples/) — coding S/M, research, documentation, multi-agent.

## Integrations

### Claude Code plugin

```
/plugin marketplace add kiloloop/agent-estimate
/plugin install agent-estimate@agent-estimate-marketplace
```

```
/estimate Add a login page with OAuth
/estimate --file spec.md
/estimate --issues 1,2,3 --repo myorg/myrepo
/estimate validate observation.yaml
/estimate calibrate
```

### GitHub Action

Available on the [GitHub Marketplace](https://github.com/marketplace/actions/agent-estimate):

```yaml
- uses: kiloloop/agent-estimate@v0
  with:
    issues: '11,12,14'
```

The report goes wherever `output-mode` points: the job summary (`summary`, the default), a PR comment (`pr-comment`), an issue comment (`issue-comment`), or a step output for downstream steps (`step-output`) — combinable with `+` (e.g. `summary+pr-comment`).

The Action accepts GitHub issue input; it has no `spec` or token-prior input.
The [forecast contract](#v08-forecast-contract) describes the CLI's
`--spec` path, expected-versus-cap scoring, and token provenance. The Action's
`expected-minutes` output is expected wall time, not an admission cap.

#### Permissions and comment identity

Grant only the permissions required by the selected output modes:

| Output mode | Required `permissions:` |
|-------------|--------------------------|
| `summary` | `issues: read` when issue input comes from a private repository |
| `pr-comment` | `issues: read` when issue input comes from a private repository, plus `pull-requests: write` |
| `issue-comment` | `issues: write` |
| `step-output` | `issues: read` when issue input comes from a private repository |

Add `contents: read` only when the calling workflow uses `actions/checkout`; the Action itself does not require a checkout. Combined modes need the union of their rows.

Use the default `${{ github.token }}` or a GitHub App installation token for
comment updates. The upsert filter selects only bot-authored comments with the
Action's marker. A personal access token (PAT) posts as its human owner, so its
comments are never selected for later updates. Repeated PAT runs create new
comments when there is no matching bot-authored comment to update. Existing
PAT-authored comments are never adopted, even by a later run using a bot token.
Granting write permissions does not change the comment author's identity.

By default, the Action installs `agent-estimate` from its own checked-out
`GITHUB_ACTION_PATH`, so the Python implementation stays coupled to the
`uses:` ref. Set `version` only when you deliberately want a published package
version instead. Each run exposes the resolved `package-version` and
`install-source`; Markdown reports repeat both values in their footer.

On offline self-hosted runners, allow the source install's isolated build
environment to resolve `hatchling>=1.32,<2` and the package dependencies from a
configured package index or cache. Merely checking out the Action does not
pre-provision the build backend used by pip's PEP 517 isolation.

<details>
<summary>Estimate on every PR</summary>

```yaml
name: Estimate
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  contents: read
  issues: read
  pull-requests: write

jobs:
  estimate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: kiloloop/agent-estimate@v0
        with:
          issues: '11,12,14'
          output-mode: summary+pr-comment
```

</details>

<details>
<summary>Auto-estimate on label</summary>

Label an issue `estimate` and the Action posts or updates one marked estimate comment (the label match is exact and case-sensitive):

```yaml
name: Auto-estimate
on:
  issues:
    types: [labeled]

permissions:
  contents: read
  issues: write

jobs:
  estimate:
    if: github.event.label.name == 'estimate'
    runs-on: ubuntu-latest
    steps:
      - uses: kiloloop/agent-estimate@v0
        with:
          issues: ${{ github.event.issue.number }}
          output-mode: issue-comment
          title: 'Agent Estimate — issue #${{ github.event.issue.number }}'
```

This repo runs it on itself — see [`.github/workflows/auto-estimate.yml`](.github/workflows/auto-estimate.yml).

</details>

<details>
<summary>Gate on the estimate (JSON step output)</summary>

With `format: json` the Action exposes `expected-minutes` as a step output — use it to gate or route downstream steps:

```yaml
name: Estimate gate
on:
  issues:
    types: [labeled]

permissions:
  issues: read

jobs:
  gate:
    if: github.event.label.name == 'estimate'
    runs-on: ubuntu-latest
    steps:
      - uses: kiloloop/agent-estimate@v0
        id: estimate
        with:
          issues: ${{ github.event.issue.number }}
          format: json
          output-mode: step-output
      - name: Flag oversized tasks
        if: steps.estimate.outputs.expected-minutes != '' && fromJSON(steps.estimate.outputs.expected-minutes) > 120
        env:
          AE_MINUTES: ${{ steps.estimate.outputs.expected-minutes }}
        run: echo "::warning::Expected ${AE_MINUTES} min — consider splitting before dispatching an agent."
```

The full JSON report is available as `steps.estimate.outputs.report` for custom processing.
Its footer records `engine_version` and `registry_version`. Agent-load rows expose
the five-minute-turn estimate as `heuristic_cost`; consumers of the removed
`estimated_cost` alias must use `heuristic_cost`.

</details>

<details>
<summary>Action inputs and outputs</summary>

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `issues` | yes | — | GitHub issue numbers (comma-separated) |
| `repo` | no | current repo | GitHub repo (owner/name) |
| `format` | no | `markdown` | Output format: `markdown` or `json` |
| `output-mode` | no | `summary` | `summary`, `pr-comment`, `issue-comment`, `step-output`, or a `+`-joined combo |
| `config` | no | — | Path to agent config YAML |
| `title` | no | `Agent Estimate Report` | Report title |
| `review-mode` | no | `standard` | Review tier: `none`, `standard`, `complex`, `3-round` |
| `spec-clarity` | no | `1.0` | Spec clarity modifier (0.3–1.3) |
| `warm-context` | no | `1.0` | Warm context modifier (0.3–1.15) |
| `agent-fit` | no | `1.0` | Agent fit modifier (0.9–1.2) |
| `task-type` | no | — | Category: `coding`, `brainstorm`, `research`, `config`, `documentation`, `frontend`, `app_dev` |
| `python-version` | no | `3.12` | Python version to use |
| `version` | no | Action ref | Published `agent-estimate` version override |
| `token` | no | `${{ github.token }}` | GitHub token |

| Output | Description |
|--------|-------------|
| `report` | Full estimation report content |
| `expected-minutes` | Expected minutes (when `format: json`) |
| `package-version` | Resolved `agent-estimate` package version used by the run |
| `install-source` | `action-path` by default, or `version-override` when `version` is set |

</details>

### Skill layout

Skills follow the [oacp-skills](https://github.com/kiloloop/oacp-skills) convention:

```
skills/estimate/
  skill.yaml            # machine-readable metadata
  README.md             # human-readable docs
  shared/INTENT.md      # shared intent across runtimes
  claude/SKILL.md       # Claude Code skill definition
  codex/SKILL.md        # Codex skill definition
```

Both runtime slices cover the same CLI (`estimate`, `validate`, `calibrate`), phrased for their respective ecosystems.

## Configuration

### Agent fleet

Pass a config to model your own fleet:

```yaml
agents:
  - name: Claude
    capabilities: [planning, implementation, review]
    parallelism: 2
    cost_per_turn: 0.12
    model_tier: frontier
  - name: Codex
    capabilities: [implementation, debugging, testing]
    parallelism: 3
    cost_per_turn: 0.08
    model_tier: production
    estimate_multiplier: 1.0
settings:
  friction_multiplier: 1.15
  inter_wave_overhead: 0.25
  metr_fallback_threshold: 45.0
```

Configs containing `settings.review_overhead` now exit 2 before model validation,
even if its value is zero or null. Delete the key and select additive review
overhead with `--review-mode` (or the request's review plan with `--spec`).

Each agent's optional `estimate_multiplier` is a finite positive number, default
`1.0`. The pipeline assigns tasks first, calls the assigned profile's
`adjust_estimate` once per task, then scales work and its PERT range by the resulting
factor. It updates wave timing, reliability warnings and heuristic cost without
reassigning tasks or scaling review overhead, inter-wave gaps, or human-equivalent
work. Structural profile plugins retain their own hook and replace same-name YAML
profiles; their result must be finite positive work (zero only for zero work).
When an adjustment changes work, reports show the applied factor and the work
minutes before and after adjustment.

With `--spec`, `task_spec.required_capabilities` must all be available on the named
configured agent. An unsatisfiable requirement exits 2 instead of selecting another
agent. Library callers can continue setting `TaskNode.required_capabilities` directly.

```bash
agent-estimate estimate "Ship packaging flow" --config ./my_agents.yaml
```

### Output formats

```bash
agent-estimate estimate "Refactor auth pipeline" --format json   # machine-readable
agent-estimate estimate --repo myorg/myrepo --issues 11,12,14    # from GitHub issues
agent-estimate estimate --file tasks.txt                          # from file
agent-estimate estimate "Follow-up fix" --history-file data.json  # auto warm-context
```

When `--warm-context` is omitted, the CLI can auto-infer it from `--history-file`;
if no history file is passed and `./data.json` exists, that file is used as the
default dispatch history source.

### Session estimates

Use `agent-estimate session` for coordinated workflows where multiple agents run
rounds of brainstorm, review, research, documentation, config, or coding work:

```bash
agent-estimate session --agents 3 --rounds 2 --type review
agent-estimate session --agents 4 --rounds 1 --per-round-minutes 25 --format json
```

The command reports wall-clock time, total agent-minutes, coordination overhead,
and per-round breakdowns.

### Calibration

Validate estimates against observed outcomes and build a calibration database:

```bash
agent-estimate validate observation.yaml --db ~/.agent-estimate/calibration.db
```

## Project

- **[Website](https://kiloloop.com/agent-estimate/)** — landing page, live demo, and the [estimate comparison view](https://kiloloop.com/agent-estimate/compare/).
- **[OACP](https://github.com/kiloloop/oacp)** — coordinate the agents you just estimated. Open Agent Coordination Protocol for multi-agent async workflows.
- **[oacp-skills](https://github.com/kiloloop/oacp-skills)** — the skill bundle agent-estimate's `/estimate` ships in.
- **[kiloloop](https://github.com/kiloloop)** — the rest of the ecosystem.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full workflow.

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
```

## Community

- [Code of Conduct](./CODE_OF_CONDUCT.md)
- [Security Policy](./SECURITY.md)
- [Support](./SUPPORT.md)
- [Changelog](./CHANGELOG.md)

## License

Apache License 2.0
