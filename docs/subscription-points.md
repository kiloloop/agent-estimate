# Subscription points (experimental)

Operators on subscription seats plan against **meter points** in a reset window,
not API dollars. This axis answers how much of a window one task consumes, given
the agent and model it is assigned to. It is **experimental and opt-in**: the
shape may change, and nothing is reported unless a caller supplies a meter table.

The package ships **no meter numbers**. Subscription meters are undocumented,
seat-specific and move on provider tier steps, so the table is the caller's own
dated reading. Points are that table applied to the
[token forecast](token-forecast-priors.md); they are local policy, not a
calibration.

## Meter table

`agent-estimate estimate --spec <request> --meter-table <file>` reads an
`agent-estimate/meter-table/v1` document. The flag requires `--spec`, because
the agent and model come from the request.
[examples/meter-table.yaml](../examples/meter-table.yaml) is the shape's
specification by example, with synthetic numbers:

```yaml
schema_version: agent-estimate/meter-table/v1
source: <where the meter readings came from>
as_of: YYYY-MM-DD
meters:
  - model_id: <the request's execution_profile.model.id>
    points_per_noncache_million: <points per million tokens that are not cache reads>
    points_per_cache_read_million: <points per million cache-read tokens>
    window_points: <size of the reset window, in points>
    window_days: <length of the reset window, in days>
    reset_anchor: <optional; one known reset instant, with a UTC offset>
```

One table describes **one seat** on one date. Coefficients are finite and
nonnegative; zero is a supplied coefficient. `window_points` and `window_days`
are positive. A model id appears once per table and is matched exactly against
`execution_profile.model.id`, never normalized or aliased. `window_days` and
`reset_anchor` describe the window and do not scale the points.

A provider tier step, a changed window or a second seat is expressed only by
supplying **another dated table**. The package never scales, ages or infers a
table, and a table carries no cache-read default.

## Derivation

Points apply the model's meter to two token slots, total and cache-read:

```text
non_cache = expected_tokens_total − expected_tokens_cache_read
expected_points = (non_cache × points_per_noncache_million
                   + expected_tokens_cache_read × points_per_cache_read_million) / 1,000,000
window_fraction = expected_points / window_points
```

Total means processed tokens including cache carry, so output tokens are charged
at the non-cache coefficient. The token forecast may be measured or a
caller-supplied prior; its basis is reported as `token_basis`, and its warnings
follow the experimental warning.

## Output

With a meter table, the report always carries a labeled block: points with
their meter, or `unavailable` with the reason. JSON reports carry it under
`forecast.subscription`; full and compact Markdown add a "Subscription Points
Forecast (experimental)" section.

```json
{
  "agent_name": "Codex",
  "model_id": "example-frontier-model",
  "token_basis": "measured",
  "expected_points": 2.9957148,
  "window_fraction": 0.029957148,
  "basis": "local-policy",
  "unavailable_reason": null,
  "source": "synthetic meter readings (example only)",
  "as_of": "2026-09-28",
  "meter": {
    "model_id": "example-frontier-model",
    "points_per_noncache_million": 2.0,
    "points_per_cache_read_million": 0.2,
    "window_points": 100.0,
    "window_days": 7.0,
    "reset_anchor": "2026-09-24T16:00:00Z"
  },
  "warnings": ["Experimental: subscription points apply a caller-supplied meter ..."]
}
```

The numbers above come from the synthetic example files and illustrate the
shape only. `source` and `as_of` are the table's.

### Unavailable

Points stay null, with `basis: unavailable` and an `unavailable_reason`, when:

- no token forecast exists (no prior and no qualifying observations);
- `execution_profile.model.id` is unknown;
- the table has no meter for that model id;
- the token forecast has no expected total, or no expected cache-read count.

No split, coefficient or model is assumed to fill a gap.

### Forecast records

Library callers build the block with `subscription_forecast` from
`agent_estimate.contract` and pass it to `forecast_from_report`. A
`ForecastRecord` validates that the block names the request's agent and model
and that its points replay from the record's token counts and the meter it
carries. Subscription points do not enter `forecast_key`; an absent block leaves
receipt hashes unchanged.

## Unchanged output

Without `--meter-table`, CLI and Action output is unchanged. The package never
discovers a meter table automatically, and the Action has no meter-table input.
