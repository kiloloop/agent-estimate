"""File-grain evidence coverage; only verified, completed executions can score.

Inputs are trusted local evidence, not authenticated execution history. Unknown
shapes stay visible and unscorable. No calibration store is opened or modified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_estimate.contract.binding import (
    BindingReceipt,
    read_binding_receipt,
    receipt_path,
)
from agent_estimate.contract.duration import score_forecast
from agent_estimate.contract.schema import ForecastRecord

_CLASSES = ("native", "cap-derived", "unavailable")
_MAX_BYTES = 2 * 1024 * 1024


class EvidenceError(ValueError):
    """An input cannot establish usable evidence."""


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise EvidenceError("invalid or duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _json_mapping(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate JSON mapping key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise EvidenceError(f"non-finite JSON number: {value}")


def _read(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EvidenceError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_BYTES:
        raise EvidenceError("record exceeds size limit")
    text = raw.decode("utf-8")
    data = (
        json.loads(text, object_pairs_hook=_json_mapping, parse_constant=_invalid_constant)
        if path.suffix == ".json"
        else yaml.load(text, Loader=_Loader)
    )
    if not isinstance(data, dict):
        raise EvidenceError("record is not a mapping")
    return data


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _label(value: object) -> str:
    return value if isinstance(value, str) and value.strip() else "unknown"


def _number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("missing UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise EvidenceError("invalid UTC timestamp")
        return parsed
    except ValueError as exc:
        raise EvidenceError("invalid UTC timestamp") from exc


def _wall_minutes(result: dict) -> float:
    """Reproduce the finalizer's active wall clock, unioning all exclusions.

    Admission idle is excluded by starting at work_started_at_utc. Peer waits
    remain included. Old records with only reported actuals have unknown basis.
    """
    start = _utc(result.get("work_started_at_utc"))
    end = _utc(result.get("completed_at_utc"))
    if end < start:
        raise EvidenceError("completion precedes start")
    intervals = []
    for field, begin_key, end_key in (
        ("pause_intervals", "paused_at_utc", "cleared_paused_at_utc"),
        ("clock_adjustments", "from_utc", "to_utc"),
    ):
        entries = result.get(field, [])
        if not isinstance(entries, list):
            raise EvidenceError("invalid clock exclusions")
        for entry in entries:
            if not isinstance(entry, dict):
                raise EvidenceError("invalid clock exclusion")
            begin, finish = _utc(entry.get(begin_key)), _utc(entry.get(end_key))
            decided, recorded = (
                _utc(entry.get("decided_at_utc")),
                _utc(entry.get("recorded_at_utc")),
            )
            if decided > recorded or finish > recorded:
                raise EvidenceError("clock exclusion precedes its evidence")
            if field == "pause_intervals":
                required = {
                    begin_key,
                    end_key,
                    "breached_fields",
                    "channel",
                    "decided_at_utc",
                    "recorded_at_utc",
                }
                axes = entry.get("breached_fields")
                if (
                    set(entry) != required
                    or not isinstance(axes, list)
                    or not axes
                    or any(not isinstance(axis, str) or not axis for axis in axes)
                    or entry.get("channel") not in ("receiver_human", "sender_reply")
                ):
                    raise EvidenceError("invalid pause provenance")
            else:
                required = {
                    begin_key,
                    end_key,
                    "actor",
                    "reason",
                    "decided_at_utc",
                    "recorded_at_utc",
                }
                actor, reason = entry.get("actor"), entry.get("reason")
                if (
                    set(entry) != required
                    or not isinstance(actor, str)
                    or not actor
                    or any(c.isspace() for c in actor)
                    or not isinstance(reason, str)
                    or not reason.strip()
                    or begin >= finish
                ):
                    raise EvidenceError("invalid clock adjustment provenance")
            intervals.append((begin, finish))
    checkpoint = _dict(result.get("threshold_checkpoint"))
    paused = checkpoint.get("paused_at_utc")
    reauth = _dict(checkpoint.get("reauthorization"))
    clear = None
    if reauth.get("disposition") == "resumed":
        clear = reauth.get("cleared_paused_at_utc")
    if clear is None and paused is not None and checkpoint.get("breached") is True:
        human = _dict(result.get("human_outcome"))
        if human.get("recorded") is True and human.get("decision") in ("approved", "modified"):
            decision = _utc(human.get("decided_at_utc"))
            declined = (
                reauth.get("decision") == "declined" or reauth.get("disposition") == "declined"
            )
            withdrawn = declined and (
                reauth.get("decided_at_utc") is None or _utc(reauth["decided_at_utc"]) >= decision
            )
            if decision >= _utc(paused) and not withdrawn:
                clear = human["decided_at_utc"]
    if paused is not None:
        # A terminal-time pause can clear after the fixed completion endpoint.
        begin = _utc(paused)
        finish = _utc(clear) if clear is not None else end
        if checkpoint.get("terminal_time") is True:
            finish = min(finish, end)
        intervals.append((begin, finish))
    elif clear is not None:
        raise EvidenceError("clear without pause")
    merged = []
    for begin, finish in sorted(intervals):
        if not start <= begin <= finish <= end:
            raise EvidenceError("clock exclusion outside work interval")
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    seconds = (end - start).total_seconds() - sum((b - a).total_seconds() for a, b in merged)
    return float(math.ceil(seconds / 60))


@dataclass
class Row:
    path: str
    task_type: str = "unknown"
    execution_profile: str = "unknown"
    classification: str = "unavailable"
    time_basis: str = "unavailable"
    censored: bool = False
    state: str = "unknown"
    reason: str = "unreadable_record"
    execution_id: str | None = None
    forecast_key: str | None = None
    actual_minutes: float | None = None
    expected_minutes: float | None = None
    ratio: float | None = None


def _walk_error(error: OSError) -> None:
    raise error


def discover_audits(root: Path, *, include_archived: bool = True) -> tuple[list[Path], int]:
    """Accept one autonomy_decisions directory or an OACP home/projects root."""
    if not root.is_dir():
        raise EvidenceError(f"audit root is not a directory: {root}")
    if root.name == "autonomy_decisions":
        directories = [root]
    else:
        projects = root / "projects" if (root / "projects").is_dir() else root
        directories = sorted(projects.glob("*/agents/*/audit/autonomy_decisions"))
    paths = set()
    for directory in directories:
        for current, dirs, files in os.walk(directory, followlinks=False, onerror=_walk_error):
            if not include_archived:
                dirs.clear()
            for filename in files:
                if filename.endswith((".yaml", ".yml", ".json")):
                    # Archive manifests are evidence indexes, not audit rows.
                    if filename in ("manifest.json", "manifest.yaml", "manifest.yml"):
                        continue
                    paths.add(Path(current) / filename)
    return sorted(paths), len(directories)


def _forecasts(directory: Path | None) -> tuple[dict[str, ForecastRecord], int]:
    if directory is None:
        return {}, 0
    candidates = defaultdict(list)
    invalid = 0
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".json", ".yaml", ".yml"):
            continue
        try:
            forecast = ForecastRecord.model_validate(_read(path))
            if forecast.forecast_id is None:
                raise EvidenceError("missing forecast id")
            candidates[forecast.forecast_id].append(forecast)
        except (OSError, ValueError, yaml.YAMLError, RecursionError):
            invalid += 1
    # Even identical copies of an id must be resolved by the caller, not guessed.
    invalid += sum(len(values) for values in candidates.values() if len(values) != 1)
    return {key: values[0] for key, values in candidates.items() if len(values) == 1}, invalid


def _normalize(path: Path, forecasts: dict, receipts: Path | None) -> Row:
    row = Row(path=str(path))
    try:
        data = _read(path)
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        return row
    result = _dict(data.get("result"))
    profile = _dict(data.get("task_profile"))
    runtime = _dict(data.get("runtime"))
    row.task_type = _label(data.get("task_type", profile.get("task_type")))
    # Never turn a message type (task_request/review_request) into a task kind.
    row.execution_profile = "legacy:" + json.dumps(
        {key: _label(runtime.get(key)) for key in ("agent", "model")},
        sort_keys=True,
        separators=(",", ":"),
    )
    row.state = _label(result.get("final_state"))
    row.execution_id = data.get("message_id") if isinstance(data.get("message_id"), str) else None
    actual = _number(result.get("actual_minutes"))
    row.actual_minutes = actual
    try:
        _utc(result.get("completed_at_utc"))
        complete = row.state in ("done", "completed", "error", "cancelled", "superseded")
    except EvidenceError:
        complete = False
    row.censored = not complete
    if actual is not None:
        row.time_basis = "reported-unknown"
    try:
        wall = _wall_minutes(result)
        if actual is not None and abs(actual - wall) <= 1:
            row.time_basis = "active-wall"
        elif actual is not None:
            row.time_basis = "inconsistent-clock"
    except EvidenceError:
        pass
    cap = _number(_dict(data.get("scope_envelope")).get("estimated_minutes"), positive=True)
    if cap is None:
        cap = _number(profile.get("estimated_minutes"), positive=True)
    row.reason = "missing_actual_or_forecast"
    if cap is not None and actual is not None:
        row.classification = "cap-derived"
        row.reason = "admission_cap_only"
    if receipts is None or not row.execution_id:
        return row
    try:
        path = receipt_path(receipts, row.execution_id)
        if not path.exists() and not path.is_symlink():
            return row
        candidate = BindingReceipt.model_validate(_read(path))
        forecast = forecasts.get(candidate.forecast_id)
        if forecast is None:
            raise EvidenceError("forecast unavailable or ambiguous")
        receipt = read_binding_receipt(path, forecast=forecast, message_id=row.execution_id)
        if result.get("work_started_at_utc") is not None and forecast.created_at_utc > _utc(
            result["work_started_at_utc"]
        ):
            raise EvidenceError("forecast issued after execution started")
        if data.get("execution_id", row.execution_id) != row.execution_id:
            raise EvidenceError("execution identity mismatch")
        if data.get("forecast_key", receipt.forecast_key) != receipt.forecast_key:
            raise EvidenceError("forecast key mismatch")
        for field, expected in (
            ("forecast_id", forecast.forecast_id),
            ("task_id", forecast.request.task_spec.task_id),
            ("execution_profile_id", forecast.request.execution_profile.execution_profile_id),
        ):
            if data.get(field, expected) != expected:
                raise EvidenceError("forecast identity mismatch")
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        row.classification = "unavailable"
        row.reason = "invalid_binding"
        return row
    row.forecast_key = receipt.forecast_key
    row.task_type = forecast.request.task_spec.task_type
    execution = forecast.request.execution_profile
    profile_json = json.dumps(
        execution.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    row.execution_profile = (
        execution.execution_profile_id
        + ":"
        + hashlib.sha256(profile_json.encode("utf-8")).hexdigest()
    )
    if (runtime.get("agent") is not None and runtime["agent"] != execution.runtime.name) or (
        execution.model.id is not None and runtime.get("model") != execution.model.id
    ):
        row.classification, row.reason = "unavailable", "execution_profile_mismatch"
        return row
    row.expected_minutes = forecast.expected_minutes
    row.classification = "unavailable"
    row.reason = "incomplete_or_incomparable_actual"
    if data.get("basis") in ("cap-derived", "cap", "declared-cap"):
        row.classification, row.reason = "cap-derived", "explicit_cap_basis"
        return row
    if (
        not complete
        or row.state not in ("done", "completed")
        or row.time_basis != "active-wall"
        or actual is None
    ):
        return row
    try:
        row.ratio = score_forecast(forecast, actual)
    except (ValueError, TypeError):
        row.reason = "missing_expected_wall"
        return row
    row.classification = "native"
    row.reason = "verified_binding_and_wall"
    return row


def backtest(
    root: Path,
    *,
    forecasts: Path | None = None,
    receipts: Path | None = None,
    include_archived: bool = True,
) -> dict:
    """Return stable JSON-ready coverage before scores; n counts unique executions."""
    for directory in (forecasts, receipts):
        if directory is not None and not directory.is_dir():
            raise EvidenceError(f"evidence directory does not exist: {directory}")
    if (forecasts is None) != (receipts is None):
        raise EvidenceError("--forecasts and --receipts must be supplied together")
    paths, directory_count = discover_audits(root, include_archived=include_archived)
    index, invalid_forecasts = _forecasts(forecasts)
    rows = [_normalize(path, index, receipts) for path in paths]
    executions = Counter(row.execution_id for row in rows if row.execution_id)
    for row in rows:
        if row.classification == "native" and executions[row.execution_id] > 1:
            row.classification, row.reason, row.ratio = "unavailable", "duplicate_execution", None
    segments = defaultdict(list)
    for row in rows:
        segments[(row.task_type, row.execution_profile)].append(row)
    coverage, scores = [], []
    for (kind, profile), members in sorted(segments.items()):
        counts = Counter(row.classification for row in members)
        ratios = sorted(row.ratio for row in members if row.classification == "native")
        coverage.append(
            {
                "task_type": kind,
                "execution_profile": profile,
                **{key: counts[key] for key in _CLASSES},
                "time_basis": dict(sorted(Counter(row.time_basis for row in members).items())),
                "censored": sum(row.censored for row in members),
                "states": dict(sorted(Counter(row.state for row in members).items())),
                "n": len(ratios),
                "minimum_n": 5,
                "qualifies": len(ratios) >= 5,
            }
        )
        if len(ratios) >= 5:
            scores.append(
                {
                    "task_type": kind,
                    "execution_profile": profile,
                    "n": len(ratios),
                    "median_actual_over_expected": _median(ratios),
                    "p80_actual_over_expected": ratios[math.ceil(0.8 * len(ratios)) - 1],
                }
            )
    counts = Counter(row.classification for row in rows)
    return {
        "coverage": coverage,
        "totals": {
            "audit_directories": directory_count,
            "records": len(rows),
            **{key: counts[key] for key in _CLASSES},
            "censored": sum(row.censored for row in rows),
        },
        "scores": scores,
        "status": "qualifying segments" if scores else "no qualifying segments",
        "invalid_forecasts": invalid_forecasts,
        "rows": [asdict(row) for row in rows],
    }


def _median(values: list[float]) -> float:
    # Averaging two large finite ratios must not overflow to infinity.
    upper = values[len(values) // 2]
    if len(values) % 2:
        return upper
    lower = values[len(values) // 2 - 1]
    return lower + (upper - lower) / 2


def render_table(report: dict) -> str:
    lines = [
        "Coverage (n = native completed executions; minimum n = 5)",
        "task_type | execution_profile | native | cap-derived | unavailable | time_basis | censored | n/5",
    ]
    for segment in report["coverage"]:
        basis = ",".join(f"{key}:{value}" for key, value in segment["time_basis"].items())
        values = [
            segment[key]
            for key in ("task_type", "execution_profile", "native", "cap-derived", "unavailable")
        ]
        lines.append(
            " | ".join(map(str, [*values, basis, segment["censored"], f"{segment['n']}/5"]))
        )
    lines.append("Totals: " + json.dumps(report["totals"], sort_keys=True))
    lines.append(report["status"])
    for score in report["scores"]:
        lines.append(json.dumps(score, sort_keys=True))
    if report["invalid_forecasts"]:
        lines.append(f"Invalid or ambiguous forecast files: {report['invalid_forecasts']}")
    return "\n".join(lines)
