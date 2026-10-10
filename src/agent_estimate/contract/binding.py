"""Deterministic forecast fingerprints and immutable author-side binding receipts.

This is a local evidence format, not a signature or proof that a dispatch ran.
The coordinator supplies the actual sent message id and retains the forecast.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from agent_estimate.contract.schema import ContractModel, ForecastRecord

Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
ReceiptId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
_MAX_RECEIPT_BYTES = 8192


class BindingReceipt(ContractModel):
    """An exact forecast artifact bound to one caller-supplied dispatch identity."""

    schema_version: Literal["agent-estimate/binding-receipt/v1"]
    forecast_id: ReceiptId
    message_id: ReceiptId
    forecast_sha256: Digest
    forecast_key: Annotated[str, Field(pattern=r"^ae-forecast-v1:[0-9a-f]{64}$", strict=True)]


class ReceiptError(ValueError):
    """Fail-closed receipt error with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _node(value: object) -> object:
    """Typed canonical tree: floats use exact binary hex, never decimal repr."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite fingerprint input")
        return ["float", (0.0 if value == 0 else value).hex()]
    if isinstance(value, list):
        return ["list", [_node(item) for item in value]]
    if isinstance(value, dict):
        # Absent and null are the same fact, so a nullable field added later with
        # a null default leaves every earlier fingerprint and receipt unchanged.
        return ["map", [[key, _node(value[key])] for key in sorted(value) if value[key] is not None]]
    raise TypeError(f"unsupported fingerprint input: {type(value).__name__}")


def _digest(domain: str, data: object) -> str:
    canonical = json.dumps([domain, _node(data)], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _forecast_data(forecast: ForecastRecord) -> dict:
    # Revalidation catches unvalidated model_copy/model_construct changes too.
    return ForecastRecord.model_validate(forecast.model_dump(mode="json")).model_dump(mode="json")


def forecast_key(forecast: ForecastRecord) -> str:
    """Hash the entire normalized request and engine; omit record time/id/outputs."""
    data = _forecast_data(forecast)
    return "ae-forecast-v1:" + _digest(
        "agent-estimate/forecast-key/v1", {key: data[key] for key in ("request", "engine")}
    )


def forecast_sha256(forecast: ForecastRecord) -> str:
    """Hash the complete normalized forecast, including identity, time and outputs."""
    return _digest("agent-estimate/forecast-artifact/v1", _forecast_data(forecast))


def _identity(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 200:
        raise ReceiptError(
            "invalid_identity", f"{name} must be a nonempty, unpadded id <= 200 chars"
        )
    return value


def receipt_path(directory: str | Path, message_id: str) -> Path:
    """One receipt per dispatch; never interpolate an untrusted id into a path."""
    message_id = _identity(message_id, "message_id")
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return Path(directory) / f"binding-{digest}.json"


def _expected(forecast: ForecastRecord, message_id: str) -> BindingReceipt:
    data = _forecast_data(forecast)
    return BindingReceipt(
        schema_version="agent-estimate/binding-receipt/v1",
        forecast_id=_identity(data["forecast_id"], "forecast_id"),
        message_id=_identity(message_id, "message_id"),
        forecast_sha256=forecast_sha256(forecast),
        forecast_key=forecast_key(forecast),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("corrupt_receipt", f"duplicate field {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReceiptError("corrupt_receipt", f"invalid JSON number {value}")


def read_binding_receipt(
    path: str | Path, *, forecast: ForecastRecord, message_id: str
) -> BindingReceipt:
    """Validate grammar and every binding against required independent expectations.

    Missing/truncated/missing-field receipts have distinct codes from corruption,
    identity mismatch, and hash mismatch. OS access errors propagate unchanged.
    """
    expected = _expected(forecast, message_id)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise ReceiptError("missing_receipt", "receipt does not exist") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReceiptError("corrupt_receipt", "receipt must be a regular file")
        # Keep ownership of the raw descriptor even if stream construction fails.
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise ReceiptError("corrupt_receipt", "receipt exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError("corrupt_receipt", "receipt is not UTF-8") from exc
    if not text.strip() or (text.lstrip().startswith("{") and not text.rstrip().endswith("}")):
        raise ReceiptError("partial_receipt", "incomplete JSON object")
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ReceiptError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ReceiptError("corrupt_receipt", "invalid JSON") from exc
    if not isinstance(data, dict):
        raise ReceiptError("corrupt_receipt", "receipt must be an object")
    missing = BindingReceipt.model_fields.keys() - data.keys()
    if missing:
        raise ReceiptError("partial_receipt", f"missing fields: {', '.join(sorted(missing))}")
    try:
        receipt = BindingReceipt.model_validate(data)
    except ValidationError as exc:
        raise ReceiptError("corrupt_receipt", "receipt schema validation failed") from exc
    for field in ("message_id", "forecast_id", "forecast_sha256", "forecast_key"):
        if getattr(receipt, field) != getattr(expected, field):
            code = f"{field}_mismatch" if field.endswith("id") else "hash_mismatch"
            raise ReceiptError(code, f"{field} does not match expected binding")
    return receipt


def write_binding_receipt(
    directory: str | Path, *, forecast: ForecastRecord, message_id: str
) -> BindingReceipt:
    """Atomically publish without replacement, fsync, and verify through the reader.

    The directory must exist on a local filesystem supporting hard links and
    directory fsync. Failures propagate; an already published receipt is retained
    on a later failure. A retry never overwrites even an identical receipt.
    """
    expected = _expected(forecast, message_id)
    target = receipt_path(directory, message_id)
    fd, temporary = tempfile.mkstemp(prefix=".binding-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((expected.model_dump_json() + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ReceiptError("receipt_exists", "refusing to replace an existing receipt") from exc
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        actual = read_binding_receipt(target, forecast=forecast, message_id=message_id)
        if actual != expected:
            raise ReceiptError(
                "readback_mismatch", "published receipt differs from intended receipt"
            )
        return actual
    finally:
        os.unlink(temporary)
