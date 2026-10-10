# Token priors and measured correction

Token forecasts start **uncalibrated**. The default typed forecast has
`tokens.basis: unavailable` and three null slots: `expected_tokens_total`,
`expected_tokens_output` and `expected_tokens_cache_read`. Total means processed
tokens, including cache carry; output and cache-read tokens are reported
separately and are each included in total. No slot is inferred from duration,
admission caps, review rounds, or the cost heuristic.

Two caller-owned inputs can fill the slots: a dated, sourced **prior**
(`basis: local-policy`), and the caller's own **observed tokens per closed leg**,
which yield a `basis: measured` forecast for the request's segment at five or
more legs. The package ships no token numbers on either path.

## Caller-supplied prior

A caller can opt in by supplying `EstimateRequest.token_prior`, validated as a
`LocalTokenPrior` from `agent_estimate.contract.schema`. It requires:

- `basis: local-policy` explicitly;
- at least one nonnegative integer count, `expected_tokens_total` or
  `expected_tokens_output`; an absent count remains null, while zero is a count;
  `expected_tokens_cache_read` may be supplied alongside;
- nonempty `source` and `population` descriptions;
- `as_of`, a calendar date in `YYYY-MM-DD` format.

When both counts exist, output must not exceed total; the same holds for
cache-read tokens. The prior adds a mandatory population mismatch warning: its
population may not match the current task and execution profile, and it is not
calibrated. Omitting `warnings` adds that warning; supplying an empty or
replacement warning is rejected. A PR-leg aggregate cannot be divided into
task-level evidence. Dated and sourced means attributable, not measured or
calibrated for this task.

The typed `ForecastRecord.tokens` block keeps token provenance separate from
duration's `basis: expected-wall`. JSON reports include the token block under
`forecast.tokens` only when opted in; full and compact Markdown report the
slots, their labels and the warning. With no prior and no observations, CLI and
Action report output is unchanged. The package never discovers a prior or an
observation file automatically.

## Rate-shape example only — not calibrated

A caller may organize its own policy by task category and a **work-minute band**:

| Key | Caller-owned policy values |
| --- | --- |
| Task category × work-minute band × execution profile | Total processed tokens per work minute; output tokens per work minute; source; date; population |

The symbolic shape is:

```text
total_count = caller_total_rate(category, work_minute_band, profile) × caller_work_minutes
output_count = caller_output_rate(category, work_minute_band, profile) × caller_work_minutes
```

This is an example shape only, **not calibrated**, and ships no numeric rates or
`token_priors.yaml`. Bands use explicit minutes; bare size letters are ambiguous.
The caller chooses any rate, minute basis and rounding policy, then submits the
resulting integer counts and provenance. Agent-estimate neither implements this
rate calculation nor chooses a band, rescales a PR-leg aggregate, or supplies a
population match.

## Measured correction

A caller can supply the observed tokens of its own closed legs and receive a
`basis: measured` forecast for the request's segment. The observations, their
provenance and their window are the caller's, produced by its own token-per-leg
join; runtime logs are never read here, and no coefficient ships with the
package.

### Observation file

`agent-estimate estimate --spec <request> --token-observations <file>` reads an
`agent-estimate/token-observations/v1` document. The flag requires `--spec`,
because the segment comes from the request.
[examples/token-observations.yaml](../examples/token-observations.yaml) is the
shape's specification by example, with synthetic counts:

```yaml
schema_version: agent-estimate/token-observations/v1
source: <where the join came from>
window:
  start: YYYY-MM-DD
  end: YYYY-MM-DD
observations:
  - task_type: coding                 # the request's task_spec.task_type
    execution_profile_id: <id>        # the request's execution_profile_id
    tokens_total: <processed tokens, cache carry included>
    tokens_output: <output tokens, included in total>
    tokens_cache_read: <optional cache-read tokens, included in total>
    forecast_key: <optional; from the binding receipt when one exists>
    execution_id: <optional; the dispatch message id; unique in the file>
```

One observation is one closed leg. `tokens_total` and `tokens_output` are
positive integers; `tokens_cache_read` may be zero or absent; output and
cache-read never exceed total. The segment key is the pair
`task_type × execution_profile_id`: a profile whose settings change needs a new
id, or its legs pool across the change. `forecast_key` and `execution_id` are
carried for joins and deduplication and do not change the fit. Nothing in the
file is a rate, and a leg is never divided into smaller tasks.

### The fit

For the request's segment with **n ≥ 5** observed legs (`MINIMUM_SEGMENT_N`,
the same bar the [backtest](backtest.md) applies to duration):

- `expected_tokens_total` and `expected_tokens_output` are the segment's
  **log-medians**: the median of `ln(count)`, exponentiated and rounded to an
  integer. An even n takes the middle pair's geometric mean.
- With a `token_prior` on the request, each slot the prior supplies is **shrunk
  toward it** with five pseudo-observations:
  `exp((n · median(ln count) + 5 · ln prior) / (n + 5))`. A segment at the bar
  sits halfway between its data and the prior; trust grows with n. A zero prior
  carries no pull. The forecast then carries a shrinkage note in `warnings`.
- `expected_tokens_cache_read` is the median observed cache-read **share**
  (`tokens_cache_read / tokens_total`) applied to the fitted total, when at least
  five legs of the segment carry a cache-read count; the share is shrunk toward
  the prior's share when the prior supplies both cache-read and total. Otherwise
  the slot stays null.
- Output is never reported above total.

Below the bar the slots stay `unavailable`, or keep the supplied prior
unchanged, with its own labels. No segment is ever reported as measured from
fewer than five legs, and no number is pooled across segments.

### Output

A measured block names its evidence: `source` and `as_of` (the window end) from
the file; `segment` with `task_type`, `execution_profile_id` and `n`; `window`
with `start` and `end`; a `population` line summarizing them; and `warnings`,
empty or the shrinkage note. JSON reports carry it under `forecast.tokens`;
Markdown adds an "Expected cache-read tokens" line and a segment line.

```json
{
  "expected_tokens_total": 5400000,
  "expected_tokens_output": 38000,
  "expected_tokens_cache_read": 4335714,
  "basis": "measured",
  "source": "synthetic token-per-leg join (example only)",
  "as_of": "2026-09-28",
  "population": "coding × example-profile-1: n=7 observed legs, window 2026-09-01..2026-09-28",
  "warnings": [],
  "segment": {"task_type": "coding", "execution_profile_id": "example-profile-1", "n": 7},
  "window": {"start": "2026-09-01", "end": "2026-09-28"}
}
```

The counts above come from the synthetic example file and illustrate the shape
only. Measured tokens do not enter `forecast_key`, which fingerprints the
request and engine; they are part of the forecast record and its receipt hash.

### Cache-read share: a third slot

The cache-read share is a **third slot**, `expected_tokens_cache_read`, on
`forecast.tokens`, not a per-model default in a meter table. It is measured per
segment from the same legs as the other two slots, so it follows the runtime and
task mix rather than a model-wide constant, and it needs no packaged or table
default. A [meter table](subscription-points.md) consumes the slot when present;
while the slot is null, subscription points stay unavailable. A
`LocalTokenPrior` may supply the slot too.

### Unchanged output

Without `--token-observations` and without a prior, CLI and Action output is
byte-identical to v0.8. A supplied prior's JSON gains the nullable
`expected_tokens_cache_read`, `segment` and `window` keys; its Markdown is
unchanged unless it supplies a cache-read count. With observations, the report
always carries a labeled token block: measured, the prior, or `unavailable`.
