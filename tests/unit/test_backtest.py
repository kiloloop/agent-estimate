"""Synthetic evidence with realistic shapes; no internal records or IDs are copied."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from typer.testing import CliRunner

from agent_estimate.backtest.reader import backtest, render_table
from agent_estimate.cli.app import app
from agent_estimate.contract import ForecastRecord, receipt_path, write_binding_receipt

START = datetime(2000, 1, 1, tzinfo=timezone.utc)


def stamp(minutes):
    return (START + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def corpus(tmp_path):
    audit = tmp_path / "projects" / "example" / "agents" / "worker" / "audit" / "autonomy_decisions"
    audit.mkdir(parents=True)
    forecasts = tmp_path / "forecasts"
    receipts = tmp_path / "receipts"
    forecasts.mkdir()
    receipts.mkdir()
    return tmp_path, audit, forecasts, receipts


def record(number, actual=20, **fields):
    return {
        "schema_version": 2,
        "spec_version": "0.5.2",
        "receiver": "worker",
        "message_id": f"msg-20000101000000-dispatcher-{number:04d}",
        "task_type": "coding",
        "runtime": {"agent": "example-runtime", "model": "example-model"},
        "task_profile": {"estimated_minutes": 120},
        "result": {
            "final_state": "done",
            "actual_minutes": actual,
            "work_started_at_utc": stamp(0),
            "completed_at_utc": stamp(actual),
        },
        **fields,
    }


def write_record(corpus, number, data=None):
    path = corpus[1] / f"audit-{number}.yaml"
    path.write_text(yaml.safe_dump(record(number) if data is None else data))
    return path


def bind(corpus, number, *, expected=20, profile_change=None):
    # Independent generated contract input, including synthetic runtime identities.
    execution = {
        "schema_version": "agent-estimate/execution-profile/v1",
        "execution_profile_id": "profile-1",
        "runtime": {"name": "example-runtime", "agent_name": "worker"},
        "model": {"id": "example-model"},
        "config_profile": {"name": "example", "revision": "1"},
        "context": {"state": "cold"},
        "execution_mode": "single",
        "review": {"mode": "single_round", "expected_rounds": 1, "intensity": "standard"},
        **(profile_change or {}),
    }
    forecast = ForecastRecord(
        schema_version="agent-estimate/forecast/v1",
        forecast_id=f"forecast-{number}",
        created_at_utc=stamp(-5),
        expected_minutes=expected,
        engine={"version": "example-v1", "registry_version": "example-v1"},
        request={
            "schema_version": "agent-estimate/estimate-request/v1",
            "task_spec": {
                "schema_version": "agent-estimate/task-spec/v1",
                "task_id": "task-1",
                "title": "Example work",
                "description": "Synthetic implementation",
                "task_type": "coding",
                "required_capabilities": [],
                "dependency_task_ids": [],
            },
            "execution_profile": execution,
            "admission": {
                "schema_version": "agent-estimate/admission-envelope/v1",
                "declared_cap_minutes": 120,
            },
        },
    )
    (corpus[2] / f"forecast-{number}.json").write_text(forecast.model_dump_json())
    write_binding_receipt(corpus[3], forecast=forecast, message_id=record(number)["message_id"])
    return forecast


def run(corpus):
    return backtest(corpus[0], forecasts=corpus[2], receipts=corpus[3])


def test_coverage_known_counts_and_scores_ignore_caps(corpus):
    for number, actual in enumerate((10, 20, 30, 40, 100), 1):
        write_record(corpus, number, record(number, actual))
        bind(corpus, number)
    for number in range(6, 16):
        write_record(corpus, number, record(number, 1000))
    write_record(corpus, 16, {"result": {"final_state": "pending"}})
    (corpus[1] / "broken.yaml").write_text("result: [")
    report = run(corpus)
    assert report["totals"] == {
        "audit_directories": 1,
        "records": 17,
        "native": 5,
        "cap-derived": 10,
        "unavailable": 2,
        "censored": 1,
    }
    assert len(report["scores"]) == 1
    assert report["scores"][0]["n"] == 5
    assert report["scores"][0]["median_actual_over_expected"] == 1.5
    assert report["scores"][0]["p80_actual_over_expected"] == 2
    assert sum(s["n"] for s in report["coverage"]) == 5
    assert all(
        sum(s[k] for k in ("native", "cap-derived", "unavailable")) >= s["n"]
        for s in report["coverage"]
    )


@pytest.mark.parametrize(
    "state", ["pending", "paused", "blocked", "error", "cancelled", "superseded"]
)
def test_non_success_states_never_score(corpus, state):
    data = record(1)
    data["result"]["final_state"] = state
    write_record(corpus, 1, data)
    bind(corpus, 1)
    row = run(corpus)["rows"][0]
    assert row["classification"] == "unavailable"
    assert row["ratio"] is None
    assert row["censored"] == (state in ("pending", "paused", "blocked"))


def test_born_done_without_completion_is_censored(corpus):
    data = record(1)
    del data["result"]["completed_at_utc"]
    write_record(corpus, 1, data)
    bind(corpus, 1)
    assert run(corpus)["rows"][0]["censored"] is True
    assert run(corpus)["totals"]["native"] == 0


@pytest.mark.parametrize("value", [True, "20", -1, float("nan"), float("inf"), None])
def test_non_numeric_or_invalid_actual_is_unavailable(corpus, value):
    data = record(1)
    data["result"]["actual_minutes"] = value
    write_record(corpus, 1, data)
    assert run(corpus)["rows"][0]["classification"] == "unavailable"


@pytest.mark.parametrize("cap", [None, 0, -1, "120", True])
def test_actual_without_positive_cap_or_receipt_is_unavailable(corpus, cap):
    write_record(corpus, 1, record(1, task_profile={"estimated_minutes": cap}))
    report = run(corpus)
    row = report["rows"][0]
    assert row["actual_minutes"] == 20
    assert row["classification"] == "unavailable"
    assert row["reason"] == "missing_actual_or_forecast"
    assert row["ratio"] is None
    assert report["scores"] == []


@pytest.mark.parametrize(
    ("envelope_cap", "profile_cap"),
    [(60, None), (60, "120"), (60, 120), (None, 120), (0, 120), ("60", 120)],
)
def test_cap_coverage_uses_envelope_or_valid_profile_fallback(
    corpus, envelope_cap, profile_cap
):
    write_record(
        corpus,
        1,
        record(
            1,
            scope_envelope={"estimated_minutes": envelope_cap},
            task_profile={"estimated_minutes": profile_cap},
        ),
    )
    report = run(corpus)
    row = report["rows"][0]
    assert row["classification"] == "cap-derived"
    assert row["reason"] == "admission_cap_only"
    # Cap selection affects coverage only; neither source supplies a score divisor.
    assert row["expected_minutes"] is None
    assert row["ratio"] is None
    assert report["scores"] == []


@pytest.mark.parametrize("agent", ["another-runtime", "worker"])
def test_runtime_agent_must_match_profile_runtime_name(corpus, agent):
    data = record(1)
    data["runtime"]["agent"] = agent
    write_record(corpus, 1, data)
    bind(corpus, 1)
    # Even matching runtime.agent_name (worker) cannot substitute for runtime.name.
    row = run(corpus)["rows"][0]
    assert row["classification"] == "unavailable"
    assert row["reason"] == "execution_profile_mismatch"
    assert row["ratio"] is None


def test_legacy_completed_and_unknown_basis_stay_distinct(corpus):
    data = record(1)
    data["result"]["final_state"] = "completed"
    del data["result"]["work_started_at_utc"]
    write_record(corpus, 1, data)
    row = run(corpus)["rows"][0]
    assert (row["classification"], row["time_basis"], row["censored"]) == (
        "cap-derived",
        "reported-unknown",
        False,
    )
    bind(corpus, 1)
    assert run(corpus)["totals"]["native"] == 0


def test_union_pause_intervals_adjustments_and_scalar(corpus):
    data = record(1, 60)
    data["result"].update(
        actual_minutes=35,
        pause_intervals=[pause(10, 20), pause(15, 25)],
        clock_adjustments=[adjustment(20, 30)],
        threshold_checkpoint={
            "paused_at_utc": stamp(40),
            "reauthorization": {"disposition": "resumed", "cleared_paused_at_utc": stamp(45)},
        },
    )
    write_record(corpus, 1, data)
    bind(corpus, 1)
    row = run(corpus)["rows"][0]
    assert row["classification"] == "native"
    assert row["ratio"] == 35 / 20


@pytest.mark.parametrize("declined", [False, True])
def test_human_pause_clear_and_declined_history(corpus, declined):
    data = record(1, 60)
    data["result"].update(
        actual_minutes=10 if declined else 50,
        threshold_checkpoint={
            "paused_at_utc": stamp(10),
            "breached": True,
            "reauthorization": {"decision": "declined", "decided_at_utc": stamp(30)}
            if declined
            else {},
        },
        human_outcome={"recorded": True, "decision": "approved", "decided_at_utc": stamp(20)},
    )
    write_record(corpus, 1, data)
    bind(corpus, 1)
    assert run(corpus)["rows"][0]["time_basis"] == "active-wall"


def test_terminal_pause_clear_clamped_and_admission_idle_excluded(corpus):
    data = record(1)
    data["created_at_utc"] = stamp(-100)
    data["result"]["threshold_checkpoint"] = {
        "paused_at_utc": stamp(20),
        "terminal_time": True,
        "reauthorization": {"disposition": "resumed", "cleared_paused_at_utc": stamp(100)},
    }
    write_record(corpus, 1, data)
    bind(corpus, 1)
    assert run(corpus)["rows"][0]["ratio"] == 1


@pytest.mark.parametrize(
    "change",
    ["clock", "backward", "pause", "binding", "key", "execution", "model", "expected", "cap"],
)
def test_invalid_or_cap_evidence_cannot_enter_native(corpus, change):
    data = record(1)
    bind(corpus, 1, expected=None if change == "expected" else 20)
    if change == "clock":
        data["result"]["actual_minutes"] = 100
    elif change == "backward":
        data["result"]["completed_at_utc"] = stamp(-1)
    elif change == "pause":
        data["result"]["pause_intervals"] = [
            {"paused_at_utc": stamp(-10), "cleared_paused_at_utc": stamp(10)}
        ]
    elif change == "binding":
        receipt_path(corpus[3], data["message_id"]).write_text("{}")
    elif change == "key":
        data["forecast_key"] = "wrong"
    elif change == "execution":
        data["execution_id"] = "other-dispatch"
    elif change == "model":
        data["runtime"]["model"] = "another-model"
    elif change == "cap":
        data["basis"] = "cap-derived"
    write_record(corpus, 1, data)
    assert run(corpus)["totals"]["native"] == 0


def test_duplicate_execution_does_not_inflate_n(corpus):
    for number in range(1, 5):
        write_record(corpus, number)
        bind(corpus, number)
    write_record(corpus, 99, record(1))
    report = run(corpus)
    assert report["totals"]["native"] == 3
    assert not report["scores"]
    assert sum(r["reason"] == "duplicate_execution" for r in report["rows"]) == 2


def test_same_profile_id_different_profile_does_not_pool(corpus):
    for number in range(1, 6):
        write_record(corpus, number)
        bind(corpus, number, profile_change={"reasoning_effort": "high" if number == 5 else "low"})
    report = run(corpus)
    assert sorted(s["n"] for s in report["coverage"]) == [1, 4]
    assert not report["scores"]


def test_corrupt_or_duplicate_forecast_does_not_bind(corpus):
    write_record(corpus, 1)
    forecast = bind(corpus, 1)
    (corpus[2] / "copy.json").write_text(forecast.model_dump_json())
    (corpus[2] / "bad.yaml").write_text("[broken")
    report = run(corpus)
    assert report["invalid_forecasts"] == 3
    assert report["totals"]["unavailable"] == 1


def test_archives_counted_and_live_only_explicit(corpus):
    path = write_record(corpus, 1)
    archive = corpus[1] / "archive" / "legacy"
    archive.mkdir(parents=True)
    path.rename(archive / path.name)
    assert run(corpus)["totals"]["records"] == 1
    assert backtest(corpus[0], include_archived=False)["totals"]["records"] == 0


def test_unreadable_duplicate_keys_and_symlinks_are_counted(corpus):
    (corpus[1] / "duplicate.yaml").write_text("result: {}\nresult: {}\n")
    (corpus[1] / "nonmapping.yaml").write_text("[]")
    (corpus[1] / "link.yaml").symlink_to(corpus[0] / "missing")
    report = run(corpus)
    assert report["totals"]["records"] == report["totals"]["unavailable"] == 3


def test_read_only_stable_cli_and_zero_exit(corpus, monkeypatch):
    monkeypatch.setenv("HOME", str(corpus[0]))
    write_record(corpus, 1)
    files = {p: p.read_bytes() for p in corpus[0].rglob("*") if p.is_file()}
    runner = CliRunner()
    args = ["backtest", str(corpus[0]), "--format", "json"]
    first, second = runner.invoke(app, args), runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0, first.output
    assert first.output == second.output
    report = json.loads(first.output)
    assert report["status"] == "no qualifying segments"
    table = runner.invoke(app, ["backtest", str(corpus[0])])
    assert table.exit_code == 0
    assert table.output.startswith("Coverage")
    assert "no qualifying segments" in table.output
    assert not (corpus[0] / ".agent-estimate").exists()
    assert files == {p: p.read_bytes() for p in corpus[0].rglob("*") if p.is_file()}
    assert render_table(report) == render_table(deepcopy(report))


def test_bad_command_inputs_return_usage_error(corpus):
    runner = CliRunner()
    for args in (
        [str(corpus[0] / "absent")],
        [str(corpus[0]), "--format", "csv"],
        [str(corpus[0]), "--forecasts", str(corpus[2])],
    ):
        assert runner.invoke(app, ["backtest", *args]).exit_code == 2


def pause(begin, end):
    return {
        "paused_at_utc": stamp(begin),
        "cleared_paused_at_utc": stamp(end),
        "breached_fields": ["actual_minutes"],
        "channel": "receiver_human",
        "decided_at_utc": stamp(end),
        "recorded_at_utc": stamp(end),
    }


def adjustment(begin, end):
    return {
        "from_utc": stamp(begin),
        "to_utc": stamp(end),
        "actor": "example-user",
        "reason": "Synthetic idle interval",
        "decided_at_utc": stamp(end),
        "recorded_at_utc": stamp(end),
    }


@pytest.mark.parametrize(
    "field,entry",
    [
        ("pause_intervals", {"paused_at_utc": stamp(10), "cleared_paused_at_utc": stamp(20)}),
        ("clock_adjustments", {"from_utc": stamp(10), "to_utc": stamp(20)}),
        ("pause_intervals", {**pause(10, 20), "channel": "unknown"}),
        ("pause_intervals", {**pause(10, 20), "breached_fields": []}),
        ("clock_adjustments", {**adjustment(10, 20), "actor": ""}),
        ("clock_adjustments", {**adjustment(10, 20), "recorded_at_utc": stamp(15)}),
    ],
)
def test_invalid_exclusion_provenance_never_scores(corpus, field, entry):
    data = record(1)
    data["result"].update(actual_minutes=10, **{field: [entry]})
    write_record(corpus, 1, data)
    bind(corpus, 1)
    assert run(corpus)["totals"]["native"] == 0


def test_conflicting_non_native_duplicate_suppresses_score(corpus):
    write_record(corpus, 1)
    data = record(1)
    data["result"]["final_state"] = "cancelled"
    write_record(corpus, 2, data)
    bind(corpus, 1)
    assert run(corpus)["totals"]["native"] == 0


def test_hindsight_forecast_never_scores(corpus):
    write_record(corpus, 1)
    forecast = bind(corpus, 1)
    data = forecast.model_dump(mode="json")
    data["created_at_utc"] = stamp(30)
    forecast = ForecastRecord.model_validate(data)
    (corpus[2] / "forecast-1.json").write_text(forecast.model_dump_json())
    receipt_path(corpus[3], record(1)["message_id"]).unlink()
    write_binding_receipt(corpus[3], forecast=forecast, message_id=record(1)["message_id"])
    assert run(corpus)["totals"]["native"] == 0


@pytest.mark.parametrize("field", ["forecast_id", "task_id", "execution_profile_id"])
def test_conflicting_optional_identity_never_scores(corpus, field):
    data = record(1, **{field: "other-identity"})
    write_record(corpus, 1, data)
    bind(corpus, 1)
    assert run(corpus)["rows"][0]["reason"] == "invalid_binding"


def test_large_finite_ratios_stay_json_serializable(corpus):
    for number in range(1, 7):
        write_record(corpus, number)
        bind(corpus, number, expected=2e-307)
    report = run(corpus)
    assert report["scores"][0]["median_actual_over_expected"] == 1e308
    json.dumps(report, allow_nan=False)
