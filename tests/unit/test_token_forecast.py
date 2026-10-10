"""Pin token honesty, caller opt-in and compatibility through the real CLI.

All counts below are synthetic test values, never packaged forecasting priors.
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
from agent_estimate.cli.app import app
from agent_estimate.cli.commands._pipeline import run_estimate_pipeline
from agent_estimate.contract.duration import forecast_from_report, score_forecast
from agent_estimate.contract.schema import (
    TOKEN_POPULATION_WARNING,
    EstimateRequest,
    ForecastRecord,
    LocalTokenPrior,
    TokenForecast,
)

ROOT = Path(__file__).resolve().parents[2]
RUNNER = CliRunner()
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


@pytest.fixture
def prior_data():
    return {
        "basis": "local-policy", "source": "synthetic test fixture",
        "as_of": "2026-09-06", "population": "synthetic PR-leg population",
        "expected_tokens_total": 1000, "expected_tokens_output": 100,
    }


@pytest.fixture
def request_data():
    return yaml.safe_load((ROOT / "examples/estimate-request.yaml").read_text())


def run_spec(tmp_path, request_data, *options):
    path = tmp_path / "request.yaml"
    path.write_text(yaml.safe_dump(request_data))
    return RUNNER.invoke(app, ["estimate", "--spec", str(path), *options])


def test_default_is_unavailable_and_cannot_emit_unlabeled_counts():
    default = TokenForecast()
    assert default.model_dump(mode="json") == {
        "expected_tokens_total": None, "expected_tokens_output": None,
        "expected_tokens_cache_read": None, "basis": "unavailable", "source": None,
        "as_of": None, "population": None, "warnings": [], "segment": None, "window": None,
    }
    for field in ("expected_tokens_total", "expected_tokens_output", "expected_tokens_cache_read"):
        for count in (0, 100):
            with pytest.raises(ValidationError, match="unavailable"):
                TokenForecast(**{field: count})


@pytest.mark.parametrize("field", ["expected_tokens_total", "expected_tokens_output"])
@pytest.mark.parametrize("value", [-1, 1.5, 1.0, True, "100", float("nan"), float("inf")])
def test_counts_require_nonnegative_integers(prior_data, field, value):
    prior_data[field] = value
    with pytest.raises(ValidationError):
        LocalTokenPrior.model_validate(prior_data)


@pytest.mark.parametrize("counts", [
    {"expected_tokens_total": 0}, {"expected_tokens_output": 0},
    {"expected_tokens_total": 1000}, {"expected_tokens_output": 100},
    {"expected_tokens_total": 100, "expected_tokens_output": 100},
])
def test_partial_and_zero_forecasts_stay_separate(prior_data, counts):
    prior_data.pop("expected_tokens_total")
    prior_data.pop("expected_tokens_output")
    prior = LocalTokenPrior(**prior_data, **counts)
    assert prior.expected_tokens_total == counts.get("expected_tokens_total")
    assert prior.expected_tokens_output == counts.get("expected_tokens_output")
    assert prior.warnings == (TOKEN_POPULATION_WARNING,)
    assert LocalTokenPrior.model_validate_json(prior.model_dump_json()) == prior
    restored = TokenForecast.model_validate_json(prior.model_dump_json())
    assert restored.model_dump() == prior.model_dump()


@pytest.mark.parametrize("field", ["basis", "source", "as_of", "population"])
@pytest.mark.parametrize("missing", [True, False])
def test_prior_requires_explicit_basis_dated_source_and_population(prior_data, field, missing):
    if missing:
        prior_data.pop(field)
    else:
        prior_data[field] = None
    with pytest.raises(ValidationError):
        LocalTokenPrior.model_validate(prior_data)


@pytest.mark.parametrize("changes", [
    {"basis": "measured"}, {"basis": "uncalibrated"}, {"source": "  "},
    {"population": ""}, {"expected_tokens_total": None, "expected_tokens_output": None},
    {"expected_tokens_total": 99}, {"warnings": []}, {"warnings": ["calibrated"]},
    {"sample_count": 5},
])
def test_prior_rejects_unsupported_or_dishonest_combinations(prior_data, changes):
    with pytest.raises(ValidationError):
        LocalTokenPrior.model_validate({**prior_data, **changes})


@pytest.mark.parametrize("value", [0, True, 1788652800, "20260906", "2026-9-6",
                                         "2026-09-06T00:00:00Z", "2026-02-30", NOW])
def test_prior_date_is_calendar_provenance_not_an_epoch(prior_data, value):
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        LocalTokenPrior.model_validate({**prior_data, "as_of": value})


def test_calendar_date_roundtrip_and_frozen_warning(prior_data):
    prior_data["as_of"] = date(2026, 9, 6)
    prior = LocalTokenPrior.model_validate(prior_data)
    assert prior.as_of == date(2026, 9, 6)
    with pytest.raises(ValidationError, match="frozen"):
        prior.warnings = ()
    restored = LocalTokenPrior.model_validate(yaml.safe_load(yaml.safe_dump(prior.model_dump(mode="json"))))
    assert restored == prior


def test_generic_forecast_cannot_bypass_the_honesty_gate(prior_data):
    with pytest.raises(ValidationError, match="population mismatch warning"):
        TokenForecast.model_validate(prior_data)
    for field in ("source", "as_of", "population"):
        data = LocalTokenPrior.model_validate(prior_data).model_dump(mode="json")
        data[field] = None
        with pytest.raises(ValidationError, match="require source"):
            TokenForecast.model_validate(data)


def test_typed_forecast_keeps_duration_caps_and_token_axes_independent(request_data, prior_data):
    cfg = load_default_config()
    cfg.agents = [cfg.agents[0]]
    report = run_estimate_pipeline(["Add validation"], cfg)
    baseline = forecast_from_report(EstimateRequest.model_validate(request_data), report,
                                    created_at_utc=NOW)
    assert baseline.tokens == TokenForecast()
    request_data["token_prior"] = prior_data
    request_data["admission"]["declared_cap_minutes"] = 10000
    forecast = forecast_from_report(EstimateRequest.model_validate(request_data), report,
                                   created_at_utc=NOW)
    assert forecast.expected_minutes == baseline.expected_minutes
    assert forecast.expected_files_touched == baseline.expected_files_touched
    assert forecast.expected_review_minutes == baseline.expected_review_minutes
    assert (forecast.basis, forecast.source, forecast.as_of) == (
        baseline.basis, baseline.source, baseline.as_of,
    )
    assert score_forecast(forecast, forecast.expected_minutes) == 1
    assert forecast.tokens.expected_tokens_total == 1000
    assert forecast.tokens.expected_tokens_output == 100
    assert forecast.tokens.basis == "local-policy"
    restored = ForecastRecord.model_validate_json(forecast.model_dump_json())
    assert restored.model_dump() == forecast.model_dump()


def test_cli_opt_in_renders_both_counts_provenance_and_warning(tmp_path, request_data, prior_data):
    baseline = run_spec(tmp_path, request_data, "--format", "json")
    assert baseline.exit_code == 0, baseline.output
    before = json.loads(baseline.stdout)
    assert "tokens" not in before["forecast"]
    request_data["token_prior"] = prior_data
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert result.exit_code == 0, result.output
    after = json.loads(result.stdout)
    tokens = after["forecast"].pop("tokens")
    assert after == before
    assert tokens == LocalTokenPrior.model_validate(prior_data).model_dump(mode="json")
    for options in ((), ("--compact",)):
        result = run_spec(tmp_path, request_data, *options)
        assert result.exit_code == 0, result.output
        assert "Expected total processed tokens (including cache carry): 1,000" in result.stdout
        assert "Expected output tokens: 100" in result.stdout
        assert "basis: `local-policy`" in result.stdout
        assert "as_of: 2026-09-06" in result.stdout
        assert prior_data["source"] in result.stdout
        assert prior_data["population"] in result.stdout
        assert TOKEN_POPULATION_WARNING in result.stdout


def test_cli_partial_forecast_does_not_invent_missing_count(tmp_path, request_data, prior_data):
    prior_data.pop("expected_tokens_total")
    prior_data["expected_tokens_output"] = 0
    request_data["token_prior"] = prior_data
    result = run_spec(tmp_path, request_data)
    assert result.exit_code == 0, result.output
    assert "including cache carry): unavailable" in result.stdout
    assert "Expected output tokens: 0" in result.stdout


@pytest.mark.parametrize("options", [(), ("--compact",), ("--format", "json")])
def test_absent_and_explicit_null_prior_have_identical_cli_bytes(tmp_path, request_data, options):
    baseline = run_spec(tmp_path, request_data, *options)
    request_data["token_prior"] = None
    result = run_spec(tmp_path, request_data, *options)
    assert baseline.exit_code == result.exit_code == 0
    assert (result.stdout, result.stderr) == (baseline.stdout, baseline.stderr)
    assert "tokens" not in result.stdout.lower()


@pytest.mark.parametrize("changes", [{"basis": "unavailable"}, {"source": None},
                                    {"as_of": 0}, {"warnings": []}, {"expected_tokens_total": True}])
def test_cli_invalid_prior_exits_two_without_output(tmp_path, request_data, prior_data, changes):
    request_data["token_prior"] = {**prior_data, **changes}
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert result.exit_code == 2
    assert "token_prior" in result.stderr
    assert result.stdout == ""
