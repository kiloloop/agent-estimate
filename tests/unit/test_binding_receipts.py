"""Evidence boundaries: deterministic inputs, exact joins, immutable publication."""

import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_estimate.contract import (
    ForecastRecord,
    OutcomeObservation,
    ReceiptError,
    binding,
    forecast_key,
    forecast_sha256,
    read_binding_receipt,
    receipt_path,
    write_binding_receipt,
)

# Realistic message-ID format with synthetic names and values; never copy a live ID.
MESSAGE = "msg-20000101000000-dispatcher-0001"


@pytest.fixture
def forecast():
    request = yaml.safe_load(
        (Path(__file__).parents[2] / "examples/estimate-request.yaml").read_text()
    )
    request["task_spec"]["task_id"] = "github:owner/repo#88"
    return ForecastRecord(
        schema_version="agent-estimate/forecast/v1",
        request=request,
        forecast_id="forecast-88-attempt-1",
        created_at_utc="2026-09-17T15:00:00Z",
        engine={"version": "0.8.0", "registry_version": "v1"},
        expected_minutes=20.5,
        expected_files_touched=3,
        expected_review_minutes=5.0,
    )


def test_id_roundtrips_and_wire_output_unchanged(forecast):
    restored = ForecastRecord.model_validate_json(forecast.model_dump_json())
    assert restored == forecast
    assert restored.request.task_spec.task_id == "github:owner/repo#88"
    assert restored.forecast_id == "forecast-88-attempt-1"
    outcome = OutcomeObservation(
        schema_version="agent-estimate/outcome-observation/v1",
        task_id=restored.request.task_spec.task_id,
        forecast_id=restored.forecast_id,
        execution_id=MESSAGE,
    )
    for wire in (
        json.loads(outcome.model_dump_json()),
        yaml.safe_load(yaml.safe_dump(outcome.model_dump(mode="json"))),
    ):
        assert OutcomeObservation.model_validate(wire) == outcome
        assert wire["execution_id"] == MESSAGE
    assert forecast.forecast_key == forecast_key(forecast)
    assert "forecast_key" not in forecast.model_dump()
    assert "forecast_key" not in json.loads(forecast.model_dump_json())


def test_key_deterministic_across_processes_and_input_spelling(forecast):
    data = forecast.model_dump(mode="json")

    # Recursive dictionary ordering and decimal spellings do not alter validated inputs.
    def reverse(value):
        if isinstance(value, dict):
            return {k: reverse(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse(v) for v in value]
        return value

    script = (
        "import sys; from agent_estimate.contract import ForecastRecord; "
        "print(ForecastRecord.model_validate_json(sys.stdin.read()).forecast_key)"
    )
    for seed, wire in [
        ("1", json.dumps(data)),
        ("999", json.dumps(reverse(data)).replace("1.0", "1e0")),
    ]:
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=wire,
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert result.stdout.strip() == forecast.forecast_key
    explicit = deepcopy(data)
    del explicit["request"]["execution_profile"]["estimate_multiplier"]
    assert ForecastRecord.model_validate(explicit).forecast_key == forecast.forecast_key
    for zero in (0.0, -0.0):
        data["request"]["admission"]["minutes_calculation"] = None
        data["expected_review_minutes"] = zero
        # Full-artifact hash also normalizes negative zero.
        candidate = ForecastRecord.model_validate(data)
        if zero == 0 and str(zero) == "0.0":
            zero_hash = forecast_sha256(candidate)
        assert forecast_sha256(candidate) == zero_hash


@pytest.mark.parametrize(
    "path,value",
    [
        (("request", "task_spec", "task_id"), "github:owner/repo#89"),
        (("request", "task_spec", "title"), "Other task"),
        (("request", "task_spec", "description"), "Changed details"),
        (("request", "task_spec", "task_type"), "documentation"),
        (("request", "task_spec", "required_capabilities"), ["python"]),
        (("request", "task_spec", "dependency_task_ids"), ["github:owner/repo#1"]),
        (("request", "task_spec", "tags"), ["evidence"]),
        (("request", "task_spec", "scope", "concerns"), 2),
        (("request", "task_spec", "source"), {"system": "github"}),
        (("request", "request_id"), "request-2"),
        (("request", "execution_profile", "execution_profile_id"), "profile-2"),
        (("request", "execution_profile", "runtime", "name"), "claude"),
        (("request", "execution_profile", "runtime", "agent_name"), "other"),
        (("request", "execution_profile", "model"), {"id": "known-model"}),
        (("request", "execution_profile", "config_profile", "revision"), "v2"),
        (("request", "execution_profile", "context", "state"), "task_warm"),
        (("request", "execution_profile", "reasoning_effort"), "high"),
        (("request", "execution_profile", "execution_mode"), "parallel"),
        (("request", "execution_profile", "estimate_multiplier"), 1.1),
        (("request", "execution_profile", "modifiers", "spec_clarity"), 0.8),
        (("request", "admission", "declared_cap_minutes"), 100),
        (("request", "admission", "declared_cap_files_touched"), 4),
        (("engine", "version"), "0.9.0"),
        (("engine", "registry_version"), "v2"),
    ],
)
def test_each_input_change_changes_key(forecast, path, value):
    data = forecast.model_dump(mode="json")
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    assert ForecastRecord.model_validate(data).forecast_key != forecast.forecast_key


@pytest.mark.parametrize(
    "field,value",
    [
        ("forecast_id", "forecast-2"),
        ("created_at_utc", "2026-09-18T15:00:00Z"),
        ("expected_minutes", 21.0),
        ("expected_files_touched", 4),
        ("expected_review_minutes", 6.0),
        ("source", "new-source"),
        ("as_of", "2026-09-17"),
    ],
)
def test_key_excludes_issuance_and_outputs_but_artifact_hash_binds_them(forecast, field, value):
    data = forecast.model_dump(mode="json")
    data[field] = value
    changed = ForecastRecord.model_validate(data)
    assert changed.forecast_key == forecast.forecast_key
    assert forecast_sha256(changed) != forecast_sha256(forecast)


def test_receipt_roundtrip_and_immutable_file(forecast, tmp_path):
    receipt = write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    path = receipt_path(tmp_path, MESSAGE)
    before = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_binding_receipt(path, forecast=forecast, message_id=MESSAGE) == receipt
    assert receipt.forecast_id == forecast.forecast_id
    assert receipt.message_id == MESSAGE
    assert receipt.forecast_key == forecast.forecast_key
    assert receipt.forecast_sha256 == forecast_sha256(forecast)
    with pytest.raises(ReceiptError) as error:
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == "receipt_exists"
    assert path.read_bytes() == before
    assert list(tmp_path.glob(".binding-*.tmp")) == []


@pytest.mark.parametrize(
    "raw,code",
    [
        (None, "missing_receipt"),
        (b"", "partial_receipt"),
        (b'{"forecast_id":', "partial_receipt"),
        (b"{broken}", "corrupt_receipt"),
        (b"\xff", "corrupt_receipt"),
        (b"[]", "corrupt_receipt"),
        (b'{"a":1,"a":2}', "corrupt_receipt"),
        (b'{"x":NaN}', "corrupt_receipt"),
        (b"x" * 8193, "corrupt_receipt"),
    ],
)
def test_missing_partial_and_corrupt(forecast, tmp_path, raw, code):
    path = tmp_path / "bad.json"
    if raw is not None:
        path.write_bytes(raw)
    with pytest.raises(ReceiptError) as error:
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == code


def test_valid_receipt_with_excess_trailing_whitespace_is_rejected(forecast, tmp_path):
    write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    path = receipt_path(tmp_path, MESSAGE)
    raw = path.read_bytes()
    path.write_bytes(raw + b" " * 9000)
    with pytest.raises(ReceiptError) as error:
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == "corrupt_receipt"


@pytest.mark.parametrize(
    "message_id", ["", " leading", "trailing ", "trailing\n", "\u00a0id", "x" * 201]
)
def test_invalid_message_ids_rejected_before_writing(forecast, tmp_path, message_id):
    with pytest.raises(ReceiptError) as path_error:
        receipt_path(tmp_path, message_id)
    assert path_error.value.code == "invalid_identity"
    with pytest.raises(ReceiptError) as write_error:
        write_binding_receipt(tmp_path, forecast=forecast, message_id=message_id)
    assert write_error.value.code == "invalid_identity"
    assert list(tmp_path.iterdir()) == []


def test_maximum_length_message_id_roundtrips(forecast, tmp_path):
    message_id = "x" * 200
    receipt = write_binding_receipt(tmp_path, forecast=forecast, message_id=message_id)
    assert (
        read_binding_receipt(
            receipt_path(tmp_path, message_id), forecast=forecast, message_id=message_id
        )
        == receipt
    )


def _descriptor_count():
    for directory in (Path("/dev/fd"), Path("/proc/self/fd")):
        if directory.is_dir():
            return len(list(directory.iterdir()))
    pytest.skip("descriptor enumeration requires /dev/fd or /proc/self/fd")


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_receipt_rejected_without_leaking_descriptors(forecast, tmp_path, kind):
    path = tmp_path / "not-a-receipt"
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    before = _descriptor_count()
    for _ in range(50):
        with pytest.raises(ReceiptError) as error:
            read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
        assert error.value.code == "corrupt_receipt"
    assert _descriptor_count() == before


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_reader_closes_descriptor_when_stream_creation_fails(
    forecast, tmp_path, monkeypatch, failure
):
    write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)

    def fail_fdopen(*args, **kwargs):
        raise failure("stream construction failed")

    before = _descriptor_count()
    monkeypatch.setattr(binding.os, "fdopen", fail_fdopen)
    with pytest.raises(failure, match="stream construction failed"):
        read_binding_receipt(receipt_path(tmp_path, MESSAGE), forecast=forecast, message_id=MESSAGE)
    assert _descriptor_count() == before


@pytest.mark.parametrize("field", list(binding.BindingReceipt.model_fields))
def test_every_receipt_field_required(forecast, tmp_path, field):
    receipt = write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    data = receipt.model_dump()
    del data[field]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ReceiptError) as error:
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == "partial_receipt"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("message_id", "msg-other", "message_id_mismatch"),
        ("forecast_id", "forecast-other", "forecast_id_mismatch"),
        ("forecast_sha256", "0" * 64, "hash_mismatch"),
        ("forecast_key", "ae-forecast-v1:" + "0" * 64, "hash_mismatch"),
        ("schema_version", "agent-estimate/binding-receipt/v2", "corrupt_receipt"),
        ("extra", True, "corrupt_receipt"),
        ("forecast_id", None, "corrupt_receipt"),
        ("message_id", 123, "corrupt_receipt"),
    ],
)
def test_receipt_rejects_invalid_bindings(forecast, tmp_path, field, value, code):
    receipt = write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    data = receipt.model_dump()
    data[field] = value
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ReceiptError) as error:
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == code


def test_reader_requires_external_identity_and_content(forecast, tmp_path):
    write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    path = receipt_path(tmp_path, MESSAGE)
    for changed, message_id, code in [
        (forecast, "msg-other", "message_id_mismatch"),
        (forecast.model_copy(update={"forecast_id": "other"}), MESSAGE, "forecast_id_mismatch"),
        (forecast.model_copy(update={"expected_minutes": 33.0}), MESSAGE, "hash_mismatch"),
    ]:
        with pytest.raises(ReceiptError) as error:
            read_binding_receipt(path, forecast=changed, message_id=message_id)
        assert error.value.code == code


def test_unvalidated_forecast_cannot_be_bound(forecast, tmp_path):
    with pytest.raises(ValidationError):
        write_binding_receipt(
            tmp_path,
            forecast=forecast.model_copy(update={"expected_minutes": float("nan")}),
            message_id=MESSAGE,
        )
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ReceiptError, match="invalid_identity"):
        write_binding_receipt(
            tmp_path, forecast=forecast.model_copy(update={"forecast_id": None}), message_id=MESSAGE
        )


def test_concurrent_writers_publish_one_complete_receipt(forecast, tmp_path):
    def write(_):
        try:
            return write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
        except ReceiptError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert results.count("receipt_exists") == 1
    assert (
        read_binding_receipt(receipt_path(tmp_path, MESSAGE), forecast=forecast, message_id=MESSAGE)
        in results
    )


def test_write_fails_closed_on_fsync_and_readback_failure(forecast, tmp_path, monkeypatch):
    original_fsync = binding.os.fsync

    def fail_fsync(fd):
        raise OSError("disk failure")

    monkeypatch.setattr(binding.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk failure"):
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(binding.os, "fsync", original_fsync)
    original_read = binding.read_binding_receipt

    def corrupt_then_read(path, **kwargs):
        Path(path).write_text('{"partial":')
        return original_read(path, **kwargs)

    monkeypatch.setattr(binding, "read_binding_receipt", corrupt_then_read)
    with pytest.raises(ReceiptError, match="partial_receipt"):
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    assert receipt_path(tmp_path, MESSAGE).exists()  # retain failed evidence; never overwrite
    assert list(tmp_path.glob(".binding-*.tmp")) == []


def test_directory_fsync_failure_retains_immutable_published_receipt(
    forecast, tmp_path, monkeypatch
):
    original_fsync = binding.os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        original_fsync(fd)

    before = _descriptor_count()
    monkeypatch.setattr(binding.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    path = receipt_path(tmp_path, MESSAGE)
    published = path.read_bytes()
    assert read_binding_receipt(path, forecast=forecast, message_id=MESSAGE).message_id == MESSAGE
    with pytest.raises(ReceiptError) as error:
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == "receipt_exists"
    assert path.read_bytes() == published
    assert list(tmp_path.glob(".binding-*.tmp")) == []
    assert _descriptor_count() == before


def test_paths_and_existing_symlinks_are_not_overwritten(forecast, tmp_path):
    assert receipt_path(tmp_path, "../../outside").parent == tmp_path
    path = receipt_path(tmp_path, MESSAGE)
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ReceiptError, match="receipt_exists"):
        write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    assert path.is_symlink()
    with pytest.raises(OSError):
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)


def test_oversized_integer_is_corrupt_on_all_supported_python_versions(forecast, tmp_path):
    receipt = write_binding_receipt(tmp_path, forecast=forecast, message_id=MESSAGE)
    raw = receipt.model_dump_json().replace('"' + MESSAGE + '"', "9" * 5000)
    path = tmp_path / "large-integer.json"
    path.write_text(raw)
    with pytest.raises(ReceiptError) as error:
        read_binding_receipt(path, forecast=forecast, message_id=MESSAGE)
    assert error.value.code == "corrupt_receipt"


def test_v1_hash_vectors(forecast):
    assert forecast.forecast_key == (
        "ae-forecast-v1:d4fbd37353e14261d6d2f9407c11bdf90cb7fc332d6f4a5eaeb5de0ba776f274"
    )
    assert forecast_sha256(forecast) == (
        "ac4891b8b6241e783e984971662019e77e9cd00527d3bbad489ca3d72e6809ec"
    )


def test_fingerprints_ignore_null_fields_so_additive_nullable_fields_are_neutral(forecast):
    # A record written before a nullable field existed hashes like one carrying
    # that field as null; a null inside a list still counts as a position.
    data = forecast.model_dump(mode="json")
    trimmed = {key: value for key, value in data.items() if value is not None}
    trimmed["tokens"] = {key: value for key, value in data["tokens"].items() if value is not None}
    assert trimmed.keys() < data.keys() and trimmed["tokens"].keys() < data["tokens"].keys()
    restored = ForecastRecord.model_validate(trimmed)
    assert restored.forecast_key == forecast.forecast_key
    assert forecast_sha256(restored) == forecast_sha256(forecast)
    assert binding._node({"a": None, "b": 1}) == binding._node({"b": 1})
    assert binding._node([None, 1]) != binding._node([1])
