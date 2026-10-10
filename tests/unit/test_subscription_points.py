"""Pin the experimental subscription-points axis: table shape, replay, labels and CLI.

Every number and model id below and in examples/meter-table.yaml is synthetic;
the fixture is the specification of the meter table's shape.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_estimate.adapters.config_loader import load_default_config
from agent_estimate.adapters.spec_loader import load_meter_table, load_token_observations
from agent_estimate.cli.app import app
from agent_estimate.cli.commands._pipeline import run_estimate_pipeline
from agent_estimate.contract import (
    EstimateRequest,
    MeterTable,
    forecast_sha256,
    measured_token_forecast,
    subscription_forecast,
)
from agent_estimate.contract.duration import forecast_from_report
from agent_estimate.contract.schema import (
    SUBSCRIPTION_METER_WARNING,
    TOKEN_POPULATION_WARNING,
    ForecastRecord,
    LocalTokenPrior,
    Meter,
    SubscriptionForecast,
    TokenForecast,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples" / "meter-table.yaml"
OBSERVATIONS = ROOT / "examples" / "token-observations.yaml"
RUNNER = CliRunner()
NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
MODEL = "example-frontier-model"


@pytest.fixture
def table():
    return load_meter_table(FIXTURE)


@pytest.fixture
def table_data():
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def request_data():
    data = yaml.safe_load((ROOT / "examples/estimate-request.yaml").read_text())
    data["execution_profile"]["model"] = {"id": MODEL}
    return data


@pytest.fixture
def prior_data():
    return {
        "basis": "local-policy", "source": "synthetic test fixture",
        "as_of": "2026-09-06", "population": "synthetic PR-leg population",
        "expected_tokens_total": 3_000_000, "expected_tokens_output": 20_000,
        "expected_tokens_cache_read": 2_000_000,
    }


@pytest.fixture
def measured(request_data):
    request = EstimateRequest.model_validate(request_data)
    return measured_token_forecast(request, load_token_observations(OBSERVATIONS))


def run_spec(tmp_path, request_data, *options, table_path=FIXTURE, observations=True):
    path = tmp_path / "request.yaml"
    path.write_text(yaml.safe_dump(request_data))
    args = ["estimate", "--spec", str(path)]
    if observations:
        args += ["--token-observations", str(OBSERVATIONS)]
    if table_path is not None:
        args += ["--meter-table", str(table_path)]
    return RUNNER.invoke(app, [*args, *options])


# --- the fixture is the table's specification ------------------------------


def test_fixture_is_one_dated_table_with_a_meter_per_model(table):
    assert table.schema_version == "agent-estimate/meter-table/v1"
    assert table.as_of == date(2026, 9, 28)
    assert [meter.model_id for meter in table.meters] == [MODEL, "example-production-model"]
    frontier = table.meter_for(MODEL)
    assert frontier.reset_anchor == datetime(2026, 9, 24, 16, tzinfo=timezone.utc)
    assert table.meter_for("example-production-model").reset_anchor is None
    # Model ids match exactly; nothing is normalized or aliased.
    assert table.meter_for(MODEL.upper()) is None
    assert table.meter_for("example-frontier") is None


@pytest.mark.parametrize("changes", [
    {"schema_version": "agent-estimate/meter-table/v2"},
    {"source": " "},
    {"source": None},
    {"as_of": "2026-09-28T00:00:00Z"},
    {"as_of": None},
    {"meters": None},
    {"extra": 1},
])
def test_table_rejects_dishonest_shapes(table_data, changes):
    with pytest.raises(ValidationError):
        MeterTable.model_validate({**table_data, **changes})


def test_table_requires_its_meters_and_unique_model_ids(table_data):
    first = table_data["meters"][0]
    with pytest.raises(ValidationError, match="unique"):
        MeterTable.model_validate({**table_data, "meters": [first, {**first, "window_days": 5}]})
    table_data.pop("meters")
    with pytest.raises(ValidationError):
        MeterTable.model_validate(table_data)


@pytest.mark.parametrize("changes", [
    {"model_id": ""}, {"model_id": None},
    {"points_per_noncache_million": -0.1}, {"points_per_cache_read_million": -1},
    {"points_per_noncache_million": "2.0"}, {"points_per_noncache_million": None},
    {"points_per_cache_read_million": float("inf")},
    {"window_points": 0}, {"window_days": 0}, {"window_days": None},
    {"reset_anchor": "2026-09-24T16:00:00"}, {"cache_default": 0.8},
])
def test_meters_require_finite_nonnegative_coefficients_and_a_window(table_data, changes):
    table_data["meters"] = [{**table_data["meters"][0], **changes}]
    with pytest.raises(ValidationError):
        MeterTable.model_validate(table_data)


def test_a_zero_coefficient_is_a_supplied_coefficient(table_data):
    table_data["meters"] = [{**table_data["meters"][0], "points_per_cache_read_million": 0}]
    meter = MeterTable.model_validate(table_data).meters[0]
    assert meter.points(1_000_000, 1_000_000) == 0
    assert meter.points(1_000_000, 0) == 2.0


# --- the arithmetic ----------------------------------------------------------


def test_points_charge_cache_reads_and_all_other_tokens_separately(table):
    meter = table.meter_for(MODEL)
    assert meter.points(0, 0) == 0
    assert meter.points(1_000_000, 0) == 2.0
    assert meter.points(1_000_000, 1_000_000) == 0.2
    assert meter.points(5_400_000, 4_335_714) == pytest.approx(
        (5_400_000 - 4_335_714) / 1e6 * 2.0 + 4_335_714 / 1e6 * 0.2
    )
    with pytest.raises(ValueError, match="cache-read"):
        meter.points(10, 11)
    with pytest.raises(ValueError, match="overflowed"):
        Meter.model_validate({**meter.model_dump(), "points_per_noncache_million": 1e308}).points(
            10**400, 0
        )


# --- the forecast and its honesty labels -----------------------------------


def test_points_replay_from_the_token_forecast_and_the_models_meter(table, request_data, measured):
    request = EstimateRequest.model_validate(request_data)
    subscription = subscription_forecast(request, measured, table)
    meter = table.meter_for(MODEL)
    assert subscription.basis == "local-policy" and subscription.token_basis == "measured"
    assert subscription.agent_name == "Codex" and subscription.model_id == MODEL
    assert subscription.meter == meter
    assert subscription.expected_points == meter.points(
        measured.expected_tokens_total, measured.expected_tokens_cache_read
    )
    assert subscription.expected_points == pytest.approx(2.9957148)
    assert subscription.window_fraction == subscription.expected_points / 100
    assert (subscription.source, subscription.as_of) == (table.source, table.as_of)
    assert subscription.warnings == (SUBSCRIPTION_METER_WARNING,)
    assert subscription.unavailable_reason is None
    restored = SubscriptionForecast.model_validate_json(subscription.model_dump_json())
    assert restored == subscription


def test_points_from_a_prior_carry_its_population_warning(table, request_data, prior_data):
    request = EstimateRequest.model_validate({**request_data, "token_prior": prior_data})
    subscription = subscription_forecast(request, request.token_prior, table)
    assert subscription.token_basis == "local-policy"
    assert subscription.expected_points == pytest.approx(1.0 * 2.0 + 2.0 * 0.2)
    assert subscription.warnings == (SUBSCRIPTION_METER_WARNING, TOKEN_POPULATION_WARNING)


def unavailable_cases(request_data, prior_data, measured):
    unknown_model = {**request_data["execution_profile"], "model": {"unknown_reason": "not exposed"}}
    other_model = {**request_data["execution_profile"], "model": {"id": "never-metered"}}
    no_cache = {key: value for key, value in prior_data.items() if key != "expected_tokens_cache_read"}
    no_total = {**no_cache, "expected_tokens_total": None}
    return [
        (request_data, None, "no token forecast"),
        (request_data, TokenForecast(), "no token forecast"),
        ({**request_data, "execution_profile": unknown_model}, measured, "model.id is unknown"),
        ({**request_data, "execution_profile": other_model}, measured, "no meter for model id"),
        (request_data, LocalTokenPrior.model_validate(no_total), "no expected total"),
        (request_data, LocalTokenPrior.model_validate(no_cache), "no expected cache-read count"),
    ]


@pytest.mark.parametrize("case", range(6))
def test_a_missing_input_is_named_and_never_assumed(table, request_data, prior_data, measured, case):
    data, tokens, reason = unavailable_cases(request_data, prior_data, measured)[case]
    subscription = subscription_forecast(EstimateRequest.model_validate(data), tokens, table)
    assert subscription.basis == "unavailable"
    assert reason in subscription.unavailable_reason
    assert subscription.expected_points is None and subscription.window_fraction is None
    assert subscription.meter is None and subscription.warnings == ()
    assert subscription.token_basis == (tokens.basis if tokens is not None else "unavailable")
    # The consulted table stays named, so an unavailable block is still attributable.
    assert (subscription.source, subscription.as_of) == (table.source, table.as_of)


def subscription_data(table, **changes):
    meter = table.meter_for(MODEL).model_dump(mode="json")
    data = {
        "agent_name": "Codex", "model_id": MODEL, "token_basis": "measured",
        "expected_points": 3.0, "window_fraction": 0.03, "basis": "local-policy",
        "source": "synthetic", "as_of": "2026-09-28", "meter": meter,
        "warnings": [SUBSCRIPTION_METER_WARNING],
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("changes", [
    {"meter": None}, {"expected_points": None}, {"window_fraction": None},
    {"source": None}, {"as_of": None}, {"as_of": "2026-09-28T00:00:00Z"},
    {"window_fraction": 0.3}, {"expected_points": -1.0},
    {"model_id": "example-production-model"}, {"model_id": None},
    {"token_basis": "unavailable"}, {"token_basis": "calibrated"},
    {"warnings": []}, {"warnings": ["Replacement warning"]},
    {"unavailable_reason": "but it has points"},
    {"basis": "measured"}, {"agent_name": ""}, {"note": "x"},
])
def test_local_policy_blocks_require_their_meter_labels_and_consistent_points(table, changes):
    assert SubscriptionForecast.model_validate(subscription_data(table)).basis == "local-policy"
    with pytest.raises(ValidationError):
        SubscriptionForecast.model_validate(subscription_data(table, **changes))


@pytest.mark.parametrize("changes", [
    {}, {"unavailable_reason": None, "expected_points": None, "window_fraction": None,
         "meter": None, "warnings": []},
    {"expected_points": None, "window_fraction": None, "warnings": []},
    {"expected_points": None, "window_fraction": None, "meter": None},
])
def test_unavailable_blocks_carry_a_reason_and_no_points(table, changes):
    data = subscription_data(table, basis="unavailable", unavailable_reason="no token forecast")
    with pytest.raises(ValidationError, match="unavailable"):
        SubscriptionForecast.model_validate({**data, **changes})


# --- the forecast record -------------------------------------------------------


@pytest.fixture
def report():
    cfg = load_default_config()
    cfg.agents = [agent for agent in cfg.agents if agent.name == "Codex"]
    return run_estimate_pipeline(["Add validation"], cfg)


def test_forecast_record_carries_points_without_changing_its_key(
    table, request_data, measured, report
):
    request = EstimateRequest.model_validate(request_data)
    baseline = forecast_from_report(request, report, created_at_utc=NOW, tokens=measured)
    subscription = subscription_forecast(request, measured, table)
    metered = forecast_from_report(
        request, report, created_at_utc=NOW, tokens=measured, subscription=subscription,
    )
    assert baseline.subscription is None and metered.subscription == subscription
    assert metered.forecast_key == baseline.forecast_key
    assert forecast_sha256(metered) != forecast_sha256(baseline)
    # An absent block is a null field, so earlier records and receipts hash as before.
    assert "subscription" in baseline.model_dump(mode="json")
    explicit = ForecastRecord.model_validate(
        {**baseline.model_dump(mode="json"), "subscription": None}
    )
    assert forecast_sha256(explicit) == forecast_sha256(baseline)
    assert ForecastRecord.model_validate_json(metered.model_dump_json()) == metered


def test_forecast_record_rejects_points_that_do_not_replay(table, request_data, measured, report):
    request = EstimateRequest.model_validate(request_data)
    subscription = subscription_forecast(request, measured, table)
    record = forecast_from_report(
        request, report, created_at_utc=NOW, tokens=measured, subscription=subscription,
    ).model_dump(mode="json")
    doubled = {**record["subscription"], "expected_points": subscription.expected_points * 2,
               "window_fraction": subscription.window_fraction * 2}
    cases = [
        ({"subscription": doubled}, "replay"),
        ({"subscription": {**record["subscription"], "agent_name": "Claude"}}, "agent and model"),
        ({"tokens": TokenForecast().model_dump(mode="json")}, "token_basis"),
        ({"tokens": {**record["tokens"], "expected_tokens_cache_read": None}}, "replay"),
        ({"tokens": {**record["tokens"], "expected_tokens_total": 6_000_000}}, "replay"),
    ]
    for changes, message in cases:
        with pytest.raises(ValidationError, match=message):
            ForecastRecord.model_validate({**record, **changes})
    unavailable = subscription_forecast(request, None, table)
    with pytest.raises(ValidationError, match="token_basis"):
        forecast_from_report(
            request, report, created_at_utc=NOW, tokens=measured, subscription=unavailable,
        )
    assert forecast_from_report(
        request, report, created_at_utc=NOW, subscription=unavailable,
    ).subscription == unavailable


# --- the CLI ---------------------------------------------------------------


def test_cli_meter_table_requires_a_spec():
    result = RUNNER.invoke(app, ["estimate", "Add validation", "--meter-table", str(FIXTURE)])
    assert result.exit_code == 2
    assert "--meter-table requires --spec" in result.stderr
    assert result.stdout == ""


def test_cli_points_json_and_markdown(tmp_path, table, request_data, measured):
    baseline = run_spec(tmp_path, request_data, "--format", "json", table_path=None)
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert baseline.exit_code == result.exit_code == 0, result.output
    before, after = json.loads(baseline.stdout), json.loads(result.stdout)
    assert "subscription" not in before["forecast"]
    subscription = after["forecast"].pop("subscription")
    assert after == before
    expected = subscription_forecast(EstimateRequest.model_validate(request_data), measured, table)
    assert subscription == expected.model_dump(mode="json")
    assert subscription["basis"] == "local-policy" and subscription["token_basis"] == "measured"
    for options in ((), ("--compact",)):
        plain = run_spec(tmp_path, request_data, *options, table_path=None)
        result = run_spec(tmp_path, request_data, *options)
        assert result.exit_code == 0, result.output
        assert "Subscription Points" not in plain.stdout
        assert "## Subscription Points Forecast (experimental)" in result.stdout
        assert f"- Agent: Codex; model: {MODEL}" in result.stdout
        assert (
            "- Expected points: 2.996 of a 100-point, 7-day window (2.996% of the window)"
            in result.stdout
        )
        assert (
            "- meter: 2 points per million non-cache tokens; "
            "0.2 points per million cache-read tokens" in result.stdout
        )
        assert (
            "- basis: `local-policy`; source: synthetic meter readings (example only); "
            "as_of: 2026-09-28; token basis: `measured`" in result.stdout
        )
        assert f"- {SUBSCRIPTION_METER_WARNING}" in result.stdout
        # Only the new section is added; every other line stands.
        added = [line for line in result.stdout.splitlines() if line not in plain.stdout.splitlines()]
        assert len(added) == 6 and added[0].startswith("## Subscription Points")


def test_cli_without_tokens_or_a_model_id_labels_unavailable(tmp_path, request_data):
    result = run_spec(tmp_path, request_data, "--format", "json", observations=False)
    assert result.exit_code == 0, result.output
    forecast = json.loads(result.stdout)["forecast"]
    assert "tokens" not in forecast
    assert forecast["subscription"]["basis"] == "unavailable"
    assert forecast["subscription"]["expected_points"] is None
    assert "no token forecast" in forecast["subscription"]["unavailable_reason"]
    request_data["execution_profile"]["model"] = {"unknown_reason": "not exposed"}
    markdown = run_spec(tmp_path, request_data)
    assert markdown.exit_code == 0, markdown.output
    assert "- Agent: Codex; model: unknown" in markdown.stdout
    assert "- Expected points: unavailable (execution_profile.model.id is unknown;" in markdown.stdout
    assert "basis: `unavailable`" in markdown.stdout
    assert SUBSCRIPTION_METER_WARNING not in markdown.stdout


def test_cli_points_from_a_prior_alone(tmp_path, request_data, prior_data):
    request_data["token_prior"] = prior_data
    result = run_spec(tmp_path, request_data, "--format", "json", observations=False)
    assert result.exit_code == 0, result.output
    subscription = json.loads(result.stdout)["forecast"]["subscription"]
    assert subscription["token_basis"] == "local-policy"
    assert subscription["expected_points"] == pytest.approx(2.4)
    assert subscription["warnings"] == [SUBSCRIPTION_METER_WARNING, TOKEN_POPULATION_WARNING]


@pytest.mark.parametrize("changes", [
    {"schema_version": "agent-estimate/token-observations/v1"},
    {"meters": [{"model_id": MODEL, "points_per_noncache_million": -1,
                 "points_per_cache_read_million": 0, "window_points": 100, "window_days": 7}]},
])
def test_cli_invalid_table_exits_two_without_output(tmp_path, request_data, table_data, changes):
    path = tmp_path / "meters.yaml"
    path.write_text(yaml.safe_dump({**table_data, **changes}))
    result = run_spec(tmp_path, request_data, "--format", "json", table_path=path)
    assert result.exit_code == 2
    assert "Meter table validation error" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("changes", [
    {"points_per_noncache_million": 1e308}, {"window_points": 1e-308},
])
def test_cli_points_that_overflow_exit_two_without_output(
    tmp_path, request_data, prior_data, table_data, changes
):
    table_data["meters"] = [{**table_data["meters"][0], **changes}]
    path = tmp_path / "meters.yaml"
    path.write_text(yaml.safe_dump(table_data))
    request_data["token_prior"] = {**prior_data, "expected_tokens_total": 10**12}
    result = run_spec(
        tmp_path, request_data, "--format", "json", table_path=path, observations=False,
    )
    assert result.exit_code == 2
    assert "Subscription forecast error" in result.stderr
    assert result.stdout == ""


def test_cli_example_command_from_the_fixture_header_runs():
    result = RUNNER.invoke(app, [
        "estimate", "--spec", str(ROOT / "examples/estimate-request.yaml"),
        "--token-observations", str(OBSERVATIONS), "--meter-table", str(FIXTURE),
        "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    subscription = json.loads(result.stdout)["forecast"]["subscription"]
    # The checked-in request declares its model unknown, as the fixture header says.
    assert subscription["basis"] == "unavailable"
    assert "model.id is unknown" in subscription["unavailable_reason"]
