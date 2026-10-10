"""Pin the measured token correction: file shape, fit, honesty labels and CLI.

Every count below and in examples/token-observations.yaml is synthetic; the
fixture is the specification of the observation file's shape.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_estimate.adapters.config_loader import load_default_config
from agent_estimate.adapters.spec_loader import load_token_observations
from agent_estimate.cli.app import app
from agent_estimate.cli.commands._pipeline import run_estimate_pipeline
from agent_estimate.contract import (
    EstimateRequest,
    TokenObservations,
    fit_segments,
    measured_token_forecast,
)
from agent_estimate.contract.duration import forecast_from_report
from agent_estimate.contract.schema import (
    MINIMUM_SEGMENT_N,
    TOKEN_POPULATION_WARNING,
    LocalTokenPrior,
    TokenForecast,
)
from agent_estimate.contract.tokens import (
    PRIOR_PSEUDO_OBSERVATIONS,
    TOKEN_SHRINKAGE_NOTE,
    fit_segment,
    log_median,
    shrunk_log_median,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples" / "token-observations.yaml"
RUNNER = CliRunner()
NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
QUALIFYING = {
    ("coding", "example-profile-1"): 7,
    ("coding", "frontier-review-v1"): 5,
    ("coding", "production-impl-v1"): 6,
    ("coding", "production-review-v1"): 8,
    ("research", "production-research-v1"): 5,
}
BELOW_BAR = ("documentation", "frontier-docs-v1")


@pytest.fixture
def observations():
    return load_token_observations(FIXTURE)


@pytest.fixture
def observation_data():
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def request_data():
    return yaml.safe_load((ROOT / "examples/estimate-request.yaml").read_text())


@pytest.fixture
def prior_data():
    return {
        "basis": "local-policy", "source": "synthetic test fixture",
        "as_of": "2026-09-06", "population": "synthetic PR-leg population",
        "expected_tokens_total": 1000, "expected_tokens_output": 100,
    }


def rows_by_segment(observations):
    segments = defaultdict(list)
    for row in observations.observations:
        segments[(row.task_type, row.execution_profile_id)].append(row)
    return segments


def independent_fit(rows):
    """Reference computation with the standard library, not the module under test."""
    total = round(math.exp(statistics.median(math.log(r.tokens_total) for r in rows)))
    output = round(math.exp(statistics.median(math.log(r.tokens_output) for r in rows)))
    cache_rows = [r for r in rows if r.tokens_cache_read is not None]
    cache_read = None
    if len(cache_rows) >= 5:
        share = statistics.median(r.tokens_cache_read / r.tokens_total for r in cache_rows)
        cache_read = round(share * total)
    return total, output, cache_read


def run_spec(tmp_path, request_data, *options, observations_path=FIXTURE):
    path = tmp_path / "request.yaml"
    path.write_text(yaml.safe_dump(request_data))
    args = ["estimate", "--spec", str(path)]
    if observations_path is not None:
        args += ["--token-observations", str(observations_path)]
    return RUNNER.invoke(app, [*args, *options])


# --- the fixture is the file's specification -------------------------------


def test_fixture_has_six_segments_and_five_qualify(observations):
    segments = rows_by_segment(observations)
    assert len(segments) == 6
    assert segments[BELOW_BAR] and len(segments[BELOW_BAR]) < MINIMUM_SEGMENT_N
    fits = fit_segments(observations)
    assert set(fits) == set(QUALIFYING)
    assert {key: fit.n for key, fit in fits.items()} == QUALIFYING
    assert observations.window.end == date(2026, 9, 28)


def test_fits_are_reproducible_from_the_fixture(observations):
    segments = rows_by_segment(observations)
    for key, fit in fit_segments(observations).items():
        total, output, cache_read = independent_fit(segments[key])
        assert (fit.expected_tokens_total, fit.expected_tokens_output) == (total, output)
        assert fit.expected_tokens_cache_read == cache_read
        assert fit.shrunk_toward_prior is False
        assert fit.expected_tokens_output <= fit.expected_tokens_total
    # One qualifying segment carries too few cache-read counts for that slot.
    assert fit_segments(observations)[("coding", "production-impl-v1")].expected_tokens_cache_read is None
    assert fit_segments(observations)[("coding", "production-impl-v1")].cache_read_n == 4


@pytest.mark.parametrize("changes", [
    {"schema_version": "agent-estimate/token-observations/v2"},
    {"source": " "},
    {"window": {"start": "2026-09-28", "end": "2026-09-01"}},
    {"window": {"start": "2026-09-01", "end": "2026-09-01T00:00:00Z"}},
    {"extra": 1},
])
def test_observation_file_rejects_dishonest_shapes(observation_data, changes):
    with pytest.raises(ValidationError):
        TokenObservations.model_validate({**observation_data, **changes})


@pytest.mark.parametrize("changes", [
    {"tokens_total": 0}, {"tokens_output": 0}, {"tokens_total": 1.0}, {"tokens_total": "100"},
    {"tokens_total": 10, "tokens_output": 11}, {"tokens_total": 10, "tokens_cache_read": 11},
    {"tokens_cache_read": -1}, {"task_type": "review"}, {"execution_profile_id": ""},
    {"tokens_total": None}, {"note": "x"},
])
def test_observation_rows_require_consistent_positive_counts(observation_data, changes):
    observation_data["observations"] = [{**observation_data["observations"][0], **changes}]
    with pytest.raises(ValidationError):
        TokenObservations.model_validate(observation_data)


def test_observation_rows_reject_duplicate_execution_ids(observation_data):
    first = observation_data["observations"][0]
    observation_data["observations"] = [first, {**first, "tokens_total": first["tokens_total"] + 1}]
    with pytest.raises(ValidationError, match="unique"):
        TokenObservations.model_validate(observation_data)
    for row in observation_data["observations"]:
        row.pop("execution_id")
    assert len(TokenObservations.model_validate(observation_data).observations) == 2


def test_zero_cache_read_is_an_observation(observation_data):
    row = {**observation_data["observations"][0], "tokens_cache_read": 0}
    row.pop("execution_id")
    observation_data["observations"] = [row]
    assert TokenObservations.model_validate(observation_data).observations[0].tokens_cache_read == 0


# --- the statistic -----------------------------------------------------------


def test_log_median_is_the_median_in_log_space():
    assert log_median([7]) == math.log(7)
    assert log_median([1, 100]) == pytest.approx(math.log(10))
    assert log_median([5, 1, 1000]) == math.log(5)
    with pytest.raises(ValueError):
        log_median([])


def test_shrinkage_uses_five_pseudo_observations_toward_the_center():
    values = [1000] * MINIMUM_SEGMENT_N
    assert shrunk_log_median(values, center=None) == math.log(1000)
    pulled = shrunk_log_median(values, center=math.log(100))
    assert PRIOR_PSEUDO_OBSERVATIONS == MINIMUM_SEGMENT_N
    assert pulled == pytest.approx((5 * math.log(1000) + 5 * math.log(100)) / 10)
    assert round(math.exp(pulled)) == 316
    more = shrunk_log_median([1000] * 45, center=math.log(100))
    assert math.log(100) < pulled < more < math.log(1000)


def make_rows(count, *, total=1000, output=100, cache_read=None, **fields):
    return TokenObservations.model_validate({
        "schema_version": "agent-estimate/token-observations/v1",
        "source": "synthetic",
        "window": {"start": "2026-09-01", "end": "2026-09-28"},
        "observations": [
            {"task_type": "coding", "execution_profile_id": "p", "tokens_total": total,
             "tokens_output": output, "tokens_cache_read": cache_read, **fields}
            for _ in range(count)
        ],
    }).observations


def test_fit_segment_below_the_bar_is_none_and_prior_slots_pull_independently(prior_data):
    assert fit_segment(make_rows(MINIMUM_SEGMENT_N - 1)) is None
    rows = make_rows(MINIMUM_SEGMENT_N, total=1000, output=100, cache_read=500)
    plain = fit_segment(rows)
    assert (plain.expected_tokens_total, plain.expected_tokens_output) == (1000, 100)
    assert plain.expected_tokens_cache_read == 500 and plain.shrunk_toward_prior is False
    prior = LocalTokenPrior.model_validate({**prior_data, "expected_tokens_total": 100,
                                            "expected_tokens_output": 100})
    pulled = fit_segment(rows, prior)
    assert pulled.expected_tokens_total == 316
    assert pulled.expected_tokens_output == 100 and pulled.shrunk_toward_prior is True
    # The prior supplies no cache-read count, so that slot is the plain share.
    assert pulled.expected_tokens_cache_read == round(0.5 * 316)
    zero = LocalTokenPrior.model_validate({**prior_data, "expected_tokens_total": 0,
                                           "expected_tokens_output": 0})
    assert fit_segment(rows, zero).expected_tokens_total == 1000
    assert fit_segment(rows, zero).shrunk_toward_prior is False


def test_cache_read_share_pulls_toward_a_prior_share_and_needs_its_own_bar(prior_data):
    rows = make_rows(MINIMUM_SEGMENT_N, total=1000, output=100, cache_read=500)
    prior = LocalTokenPrior.model_validate({**prior_data, "expected_tokens_total": 1000,
                                            "expected_tokens_output": 100,
                                            "expected_tokens_cache_read": 100})
    fit = fit_segment(rows, prior)
    assert fit.expected_tokens_total == 1000
    assert fit.expected_tokens_cache_read == round(((5 * 0.5) + (5 * 0.1)) / 10 * 1000)
    partial = [*make_rows(4, cache_read=500), *make_rows(1)]
    assert fit_segment(partial).expected_tokens_cache_read is None
    assert fit_segment(partial).cache_read_n == 4


def test_output_is_never_reported_above_total(prior_data):
    rows = make_rows(MINIMUM_SEGMENT_N, total=1000, output=1000)
    prior = LocalTokenPrior.model_validate({**prior_data, "expected_tokens_total": 10,
                                            "expected_tokens_output": None})
    fit = fit_segment(rows, prior)
    assert fit.expected_tokens_total < 1000
    assert fit.expected_tokens_output == fit.expected_tokens_total


def test_fit_segment_refuses_mixed_segments(observations):
    with pytest.raises(ValueError, match="one segment"):
        fit_segment(list(observations.observations))


# --- the forecast and its honesty labels -----------------------------------


def test_measured_forecast_names_segment_window_and_source(observations, request_data):
    request = EstimateRequest.model_validate(request_data)
    tokens = measured_token_forecast(request, observations)
    fit = fit_segments(observations)[("coding", "example-profile-1")]
    assert tokens.basis == "measured"
    assert tokens.expected_tokens_total == fit.expected_tokens_total
    assert tokens.expected_tokens_output == fit.expected_tokens_output
    assert tokens.expected_tokens_cache_read == fit.expected_tokens_cache_read
    assert tokens.segment.model_dump() == {
        "task_type": "coding", "execution_profile_id": "example-profile-1", "n": 7,
    }
    assert tokens.window == observations.window
    assert tokens.as_of == observations.window.end
    assert tokens.source == observations.source
    assert tokens.warnings == ()
    assert "n=7" in tokens.population
    restored = TokenForecast.model_validate_json(tokens.model_dump_json())
    assert restored == tokens


def test_measured_forecast_with_a_prior_is_pulled_and_labeled(observations, request_data, prior_data):
    request = EstimateRequest.model_validate({**request_data, "token_prior": prior_data})
    tokens = measured_token_forecast(request, observations)
    plain = measured_token_forecast(EstimateRequest.model_validate(request_data), observations)
    assert tokens.basis == "measured"
    assert tokens.warnings == (TOKEN_SHRINKAGE_NOTE,)
    assert prior_data["expected_tokens_total"] < tokens.expected_tokens_total < plain.expected_tokens_total


def test_below_the_bar_keeps_the_prior_or_stays_unavailable(observations, request_data, prior_data):
    request_data["task_spec"]["task_type"], request_data["execution_profile"]["execution_profile_id"] = BELOW_BAR
    request = EstimateRequest.model_validate(request_data)
    assert measured_token_forecast(request, observations) == TokenForecast()
    with_prior = EstimateRequest.model_validate({**request_data, "token_prior": prior_data})
    assert measured_token_forecast(with_prior, observations) is with_prior.token_prior
    unknown = EstimateRequest.model_validate({
        **request_data,
        "execution_profile": {**request_data["execution_profile"], "execution_profile_id": "never-seen"},
    })
    assert measured_token_forecast(unknown, observations) == TokenForecast()


def measured_data(**changes):
    data = {
        "expected_tokens_total": 5000, "expected_tokens_output": 50, "basis": "measured",
        "source": "synthetic join", "as_of": "2026-09-28",
        "segment": {"task_type": "coding", "execution_profile_id": "p", "n": 5},
        "window": {"start": "2026-09-01", "end": "2026-09-28"},
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not ...}


@pytest.mark.parametrize("changes", [
    {"segment": None}, {"window": None}, {"source": None}, {"as_of": None},
    {"as_of": "2026-09-27"},
    {"segment": {"task_type": "coding", "execution_profile_id": "p", "n": 4}},
    {"expected_tokens_total": None, "expected_tokens_output": None},
    {"expected_tokens_cache_read": 5001}, {"expected_tokens_output": 5001},
    {"segment": {"task_type": "review", "execution_profile_id": "p", "n": 5}},
])
def test_measured_forecasts_require_segment_window_and_consistent_counts(changes):
    assert TokenForecast.model_validate(measured_data()).basis == "measured"
    with pytest.raises(ValidationError):
        TokenForecast.model_validate(measured_data(**changes))


def test_measured_forecast_may_omit_population_and_carry_the_note():
    tokens = TokenForecast.model_validate(measured_data(population=..., warnings=[TOKEN_SHRINKAGE_NOTE]))
    assert tokens.population is None and tokens.warnings == (TOKEN_SHRINKAGE_NOTE,)


@pytest.mark.parametrize("changes", [
    {"segment": {"task_type": "coding", "execution_profile_id": "p", "n": 5}},
    {"window": {"start": "2026-09-01", "end": "2026-09-28"}},
])
def test_local_policy_priors_cannot_claim_a_measured_segment(prior_data, changes):
    with pytest.raises(ValidationError, match="measured segment"):
        LocalTokenPrior.model_validate({**prior_data, **changes})


def test_local_policy_prior_may_supply_the_cache_read_slot(prior_data):
    prior = LocalTokenPrior.model_validate({**prior_data, "expected_tokens_cache_read": 600})
    assert prior.expected_tokens_cache_read == 600
    assert prior.warnings == (TOKEN_POPULATION_WARNING,)
    with pytest.raises(ValidationError, match="cache-read"):
        LocalTokenPrior.model_validate({**prior_data, "expected_tokens_cache_read": 1001})


@pytest.mark.parametrize("field,value", [
    ("expected_tokens_cache_read", 0), ("segment", {"task_type": "coding",
                                                    "execution_profile_id": "p", "n": 5}),
    ("window", {"start": "2026-09-01", "end": "2026-09-28"}),
])
def test_unavailable_forecasts_carry_no_measured_evidence(field, value):
    with pytest.raises(ValidationError, match="unavailable"):
        TokenForecast(**{field: value})


def test_forecast_record_carries_measured_tokens_without_changing_its_key(observations, request_data):
    cfg = load_default_config()
    cfg.agents = [cfg.agents[0]]
    report = run_estimate_pipeline(["Add validation"], cfg)
    request = EstimateRequest.model_validate(request_data)
    baseline = forecast_from_report(request, report, created_at_utc=NOW)
    measured = forecast_from_report(
        request, report, created_at_utc=NOW,
        tokens=measured_token_forecast(request, observations),
    )
    assert baseline.tokens == TokenForecast()
    assert measured.tokens.basis == "measured"
    assert measured.forecast_key == baseline.forecast_key
    assert measured.expected_minutes == baseline.expected_minutes


# --- the CLI ---------------------------------------------------------------


def test_cli_observations_require_a_spec():
    result = RUNNER.invoke(app, ["estimate", "Add validation", "--token-observations", str(FIXTURE)])
    assert result.exit_code == 2
    assert "--token-observations requires --spec" in result.stderr
    assert result.stdout == ""


def test_cli_measured_json_and_markdown(tmp_path, request_data, observations):
    baseline = run_spec(tmp_path, request_data, "--format", "json", observations_path=None)
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert baseline.exit_code == result.exit_code == 0, result.output
    before, after = json.loads(baseline.stdout), json.loads(result.stdout)
    tokens = after["forecast"].pop("tokens")
    assert after == before
    expected = measured_token_forecast(EstimateRequest.model_validate(request_data), observations)
    assert tokens == expected.model_dump(mode="json")
    assert tokens["basis"] == "measured" and tokens["segment"]["n"] == 7
    for options in ((), ("--compact",)):
        result = run_spec(tmp_path, request_data, *options)
        assert result.exit_code == 0, result.output
        assert f"(including cache carry): {expected.expected_tokens_total:,}" in result.stdout
        assert f"Expected output tokens: {expected.expected_tokens_output:,}" in result.stdout
        assert f"Expected cache-read tokens: {expected.expected_tokens_cache_read:,}" in result.stdout
        assert "basis: `measured`" in result.stdout
        assert "segment: coding × example-profile-1; n: 7; window: 2026-09-01..2026-09-28" in result.stdout
        assert TOKEN_POPULATION_WARNING not in result.stdout


def test_cli_below_the_bar_labels_unavailable_or_keeps_the_prior(tmp_path, request_data, prior_data):
    request_data["task_spec"]["task_type"], request_data["execution_profile"]["execution_profile_id"] = BELOW_BAR
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["forecast"]["tokens"] == TokenForecast().model_dump(mode="json")
    markdown = run_spec(tmp_path, request_data)
    assert "basis: `unavailable`" in markdown.stdout
    assert "Expected cache-read tokens" not in markdown.stdout
    request_data["token_prior"] = prior_data
    result = run_spec(tmp_path, request_data, "--format", "json")
    assert result.exit_code == 0, result.output
    tokens = json.loads(result.stdout)["forecast"]["tokens"]
    assert tokens == LocalTokenPrior.model_validate(prior_data).model_dump(mode="json")
    assert tokens["basis"] == "local-policy" and tokens["segment"] is None


def test_cli_prior_markdown_is_unchanged_without_a_cache_read_count(tmp_path, request_data, prior_data):
    request_data["token_prior"] = prior_data
    result = run_spec(tmp_path, request_data, observations_path=None)
    assert result.exit_code == 0, result.output
    assert "Expected cache-read tokens" not in result.stdout
    assert "segment:" not in result.stdout
    request_data["token_prior"] = {**prior_data, "expected_tokens_cache_read": 600}
    result = run_spec(tmp_path, request_data, observations_path=None)
    assert "Expected cache-read tokens: 600" in result.stdout


@pytest.mark.parametrize("changes", [
    {"schema_version": "agent-estimate/estimate-request/v1"},
    {"observations": [{"task_type": "coding", "execution_profile_id": "p",
                       "tokens_total": 10, "tokens_output": 11}]},
])
def test_cli_invalid_observations_exit_two_without_output(tmp_path, request_data, observation_data, changes):
    path = tmp_path / "observations.yaml"
    path.write_text(yaml.safe_dump({**observation_data, **changes}))
    result = run_spec(tmp_path, request_data, "--format", "json", observations_path=path)
    assert result.exit_code == 2
    assert "Token observations validation error" in result.stderr
    assert result.stdout == ""


def test_cli_example_command_from_the_fixture_header_runs():
    result = RUNNER.invoke(app, [
        "estimate", "--spec", str(ROOT / "examples/estimate-request.yaml"),
        "--token-observations", str(FIXTURE), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["forecast"]["tokens"]["basis"] == "measured"
