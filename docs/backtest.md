# Backtest audit evidence

`agent-estimate backtest` is a read-only reader. It prints coverage before scores
and never opens the calibration database. An empty corpus or zero qualifying
segments is a successful result (exit 0, `no qualifying segments`).

```bash
# Read all project/agent autonomy_decisions directories, including archives.
agent-estimate backtest "$OACP_HOME"

# Or read one audit directory, supplying retained forecast artifacts and receipts.
agent-estimate backtest ./autonomy_decisions \
  --forecasts ./forecasts --receipts ./receipts --format json

# Explicitly omit archived records when comparing the active corpus.
agent-estimate backtest "$OACP_HOME" --live-only
```

The positional root accepts an OACP home, its `projects` directory, or one
`autonomy_decisions` directory. Audit `.yaml`, `.yml`, and `.json` files are
counted recursively, including historical archives. `manifest.json`,
`manifest.yaml`, and `manifest.yml` are archive indexes and are excluded.
Symlinked directories below each discovered `autonomy_decisions` directory are
not followed; unreadable or symlinked record files count as unavailable.
Invalid root/options or inaccessible directories return
exit 2 rather than claiming a complete scan. Reading an unchanged corpus gives
byte-identical output: there is no current-time field or elapsed-time update.

## Classification and joins

The unit of coverage is one audit file. Every file belongs to exactly one class:

| Class | Evidence |
| --- | --- |
| `native` | A validated forecast with independent expected wall minutes, a verified binding receipt for the audit's message ID, successful finalized work, and comparable measured wall actuals. |
| `cap-derived` | Finite nonnegative reported actual minutes and a positive admission cap, without a usable native forecast; explicit cap-basis records also remain in this class. No cap divisor is guessed and no cap-derived ratio is scored. |
| `unavailable` | Unreadable/malformed records, missing expectations/actuals, invalid bindings, ambiguous execution identities, or bound but incomplete/incomparable outcomes. |

Supply `--forecasts` and `--receipts` together to enable native joins. Forecasts
are retained `ForecastRecord` JSON/YAML files, directly inside the forecast
directory. Receipts use the existing [binding receipt](binding-receipts.md)
filename and validation API. The reader indexes independently retained forecasts
by `forecast_id`, then verifies the full artifact digest and `forecast_key`
against the receipt. A duplicate forecast ID is ambiguous even if the copies
are identical. Invalid/ambiguous forecast-file counts are printed separately;
affected audit rows are unavailable.

The receipt's `message_id` joins to audit `message_id`: this is the contract's
`execution_id`, not the audit's `evaluation_id`. The audit's `result` supplies
the outcome. If an audit explicitly carries `execution_id` or `forecast_key`,
it must agree with the verified binding. A forecast issued after the recorded
work start cannot score. No receipt means segment-only attribution; the reader
never links by file name, similar task text, or the newest forecast. Receipts
are local evidence, not authentication of execution history.

A bound segment uses `TaskSpec.task_type` and the full normalized
`ExecutionProfile` (displayed as its ID plus a SHA-256 digest). Reusing a profile
ID with different settings does not pool samples. Known runtime-agent mismatch,
or a missing/different actual model when the forecast names a model, prevents
native classification. When present, audit `runtime.agent` must equal
`ExecutionProfile.runtime.name`; audit `runtime.model` must equal
`ExecutionProfile.model.id` when the forecast names a model.
Unbound records use explicit `task_type` when present,
and a `legacy:` runtime-agent/model segment. Missing fields remain `unknown`;
a message type such as `task_request` does not establish a task kind.

Multiple audit files with one execution ID remain counted, but none can add a
native sample. This deliberately avoids selecting an arbitrary evaluation,
archive copy, or attempted execution as the outcome.

## Timing and censoring

The reader normalizes by field shape, not spec-version labels, because writer
features appeared within older protocol stamp windows:

- `active-wall`: work start to completion, excluding the union of explicit
  checkpoint reauthorization pauses, retired `pause_intervals`, and
  `clock_adjustments`. Overlap is deducted once; round up once to whole minutes.
  Reported `actual_minutes` must agree within the finalizer's one-minute tolerance.
  Admission idle is excluded; peer-review wait remains included.
- `reported-unknown`: numeric actuals without a defensible work-start clock.
  These remain useful coverage, but cannot become native wall observations.
- `inconsistent-clock`: reported actuals disagree with the normalized clock.
- `unavailable`: no finite, nonnegative numeric actual. Strings and booleans are
  not duration values.

A scalar pause clears through resumed checkpoint reauthorization or a governing
approved/modified human outcome. An unanswered or declined pause runs to the
completion endpoint. A terminal-time pause can clear after pinned completion;
its exclusion is clamped to that endpoint.

Completion requires a timestamp and a terminal state. Legacy `completed` is
recognized alongside `done`; old born-`done` records without a completion stamp
are censored. Open executions are counted as censored without inventing an
endpoint at the time of the scan. Cancelled, failed (`error`), and superseded
records never enter native scores even when their clock is measurable. The
JSON coverage includes state counts, and per-file rows retain reasons and links.

## Scores

Only segments with **at least five unique native completed executions** score.
The table reports `n/5` alongside native, cap-derived, unavailable, time-basis,
and censored counts. `n` belongs to that segment; cap-derived rows cannot raise
it. Scores are the median actual/expected ratio and the nearest-rank p80 ratio
(sorted index `ceil(0.8*n)-1`). JSON includes coverage, totals, scores, and
per-file explanations. No token correction, database update, or calibration
claim follows from this command.
