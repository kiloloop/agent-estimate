# Forecast keys and binding receipts

The library connects a forecast to the dispatch whose outcome will be scored.
The coordinator writes the receipt **after** a successful dispatch, using the
returned OACP message ID. Sending dispatches, proving execution, ingesting outcomes,
and changing the calibration database are outside this API.

The existing contract keeps three separate identities:

- `TaskSpec.task_id` identifies logical work, for example `github:owner/repo#88`.
- `ForecastRecord.forecast_id` identifies a particular forecast artifact.
- `OutcomeObservation.execution_id` is the OACP message ID of the dispatch that
  ran the work. It joins to the receipt's `message_id`.

Task and forecast IDs remain caller-owned contract identifiers; this library
neither creates IDs nor changes their existing validation. A receipt requires a
non-null forecast ID and a nonempty, unpadded message ID (at most 200 characters).
A caller must supply the actual sent ID; the library cannot authenticate it.

## Forecast key

`ForecastRecord.forecast_key` (also `forecast_key(record)`) returns
`ae-forecast-v1:<64 lowercase SHA-256 hex digits>`. It is a property, not a
serialized model field, so existing forecast JSON, CLI, and Action output stays
unchanged. Receipts persist the key for downstream joins.

The hash input is exactly the complete, validated, default-expanded
`{"request": record.request, "engine": record.engine}` mapping. Thus all task
facts, request/profile IDs, capabilities, modifiers, context, review plan,
admission metadata, token priors, and engine/registry versions participate.
Admission changes create different keys; this does **not** make caps scoring
inputs. Record `forecast_id`, `created_at_utc`, and forecast outputs/provenance
are excluded. Re-issuing a forecast for identical inputs has the same key;
changing its outputs alone does not change the key. Use the full-artifact hash
to detect that difference. A future engine/registry revision must change its
reported version if its behavior changes.

## Hash encoding (version 1)

Both hashes revalidate the forecast through `ForecastRecord`, then use
`model_dump(mode="json")`, including defaults. Object members whose value is
null are omitted from the tree: absent and null are the same fact, so a nullable
field added to the contract later with a null default leaves every earlier
fingerprint and receipt unchanged. A null inside an array keeps its position.
Timestamps normalize to UTC under the contract. JSON object insertion order and equivalent numeric
spellings disappear during validation. Ordered arrays remain ordered; string
case and Unicode normalization are not changed beyond existing contract rules.

The digest is SHA-256 over ASCII JSON of `[domain, node(data)]`, serialized with
`ensure_ascii=True` and separators `(',', ':')`. The typed tree is:

| Value | `node(value)` |
| --- | --- |
| null (array element) | `["null"]` |
| boolean | `["bool", value]` |
| string | `["str", value]` |
| integer | `["int", decimal_integer_string]` |
| finite float | `["float", value.hex()]` (both signed zeros use positive zero) |
| array | `["list", [node(element), ...]]` |
| object | `["map", [[key, node(value)], ...]]`, keys sorted lexicographically, null-valued members omitted |

Floats therefore use exact binary values rather than a decimal representation
or rounding precision. Contract float fields normalize integer inputs to floats;
integer fields remain integers. No Python `hash()` or process-specific state is
used. A changed encoding requires a new version/domain.

For `forecast_key`, the domain is `agent-estimate/forecast-key/v1` and `data`
is the request/engine mapping above. For `forecast_sha256(record)`, the domain
is `agent-estimate/forecast-artifact/v1` and `data` is the **entire** normalized
forecast, including its ID, UTC creation time, request, engine, all expected
values, token forecast, and provenance. This is a semantic artifact hash, not a
hash of the original JSON/YAML file bytes: formatting changes are irrelevant.

## Receipt format and API

```python
from pathlib import Path
from agent_estimate.contract import (
    ForecastRecord, read_binding_receipt, receipt_path, write_binding_receipt,
)

forecast = ForecastRecord.model_validate_json(Path("forecast.json").read_text())
# forecast.forecast_id must already be set by the caller.
# Realistic message-ID format with synthetic values; use the actual send result in production.
message_id = "msg-20000101000000-dispatcher-0001"
receipts = Path("receipts")  # caller-created, trusted local directory
receipt = write_binding_receipt(receipts, forecast=forecast, message_id=message_id)
verified = read_binding_receipt(
    receipt_path(receipts, message_id), forecast=forecast, message_id=message_id,
)
assert verified == receipt
```

A receipt is a UTF-8 JSON object with **exactly** these required fields:

| Field | Value |
| --- | --- |
| `schema_version` | `agent-estimate/binding-receipt/v1` |
| `forecast_id` | Exact ID from the validated forecast |
| `message_id` | Caller-supplied sent dispatch ID |
| `forecast_sha256` | Full normalized artifact digest described above |
| `forecast_key` | Versioned request/engine fingerprint described above |

Naming is `binding-<sha256(message_id.encode('utf-8'))>.json`, one receipt per
dispatch. Raw IDs are never interpolated into paths. Retries use a new dispatch
ID; writing a second receipt for an existing dispatch always refuses, even if
its contents would be identical. Reforecasts retain separate forecast IDs.
The reader accepts an explicit path but always requires the independently known
forecast and expected message ID. Reading embedded claims alone is insufficient.

The writer creates a private temporary file in the existing directory, writes
and fsyncs all bytes, publishes with an atomic no-replacement hard link, fsyncs
the directory, and reads back through the same verifying reader. It returns
only after equality with the intended receipt. Concurrent writers produce one
winner; readers never observe an in-progress normal publication. Temporary
files are removed on ordinary completion or failure. A failure after publication
leaves the published evidence in place and raises; a retry still refuses to
overwrite. The coordinator must resolve that failure before using the receipt.
Published receipts retain the temporary file's owner-only permissions (mode
`0600`); the coordinator and reader must run as the same local user.

This API requires a trusted directory on a local POSIX filesystem supporting
hard links, `O_NOFOLLOW`, and directory fsync. OS errors propagate; there is no
weaker persistence fallback. It protects against accidental corruption,
incomplete writes, wrong joins, and concurrent publication, not a hostile
filesystem owner or forged execution history. Receipts are not signatures.

## Failure modes

`ReceiptError` derives from `ValueError` and exposes a stable `code`:

| Code | Meaning |
| --- | --- |
| `invalid_identity` | Required caller identity missing, empty, padded, or too long |
| `missing_receipt` | No receipt at the requested path |
| `partial_receipt` | Empty/incomplete JSON object or missing required field |
| `corrupt_receipt` | Invalid UTF-8/JSON, duplicate keys, wrong types/version, extra fields, non-regular file, or more than 8 KiB |
| `message_id_mismatch` | Receipt is for a different dispatch |
| `forecast_id_mismatch` | Receipt is for a different forecast ID |
| `hash_mismatch` | Full artifact digest or forecast key differs |
| `receipt_exists` | Immutable destination already exists (including dangling symlinks) |
| `readback_mismatch` | Writer's final read-back differs from intended receipt |

Malformed JSON that is not recognizably an incomplete object is classified as
corruption. Identity checks precede hash checks. Contract validation failures
raise Pydantic `ValidationError`; permission, symlink refusal, and filesystem
I/O errors remain `OSError`. None of these failures returns a usable receipt.
