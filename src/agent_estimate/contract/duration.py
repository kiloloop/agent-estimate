"""Duration forecasts and scoring, with admission budgets kept out of the denominator."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent_estimate.contract.schema import (
    EstimateRequest,
    ForecastRecord,
    SubscriptionForecast,
    TokenForecast,
)

if TYPE_CHECKING:
    from agent_estimate.render.report_models import EstimationReport


def forecast_from_report(
    request: EstimateRequest,
    report: EstimationReport,
    *,
    created_at_utc: datetime,
    tokens: TokenForecast | None = None,
    subscription: SubscriptionForecast | None = None,
) -> ForecastRecord:
    """Build one typed forecast from a single-task report, without reading caps.

    Time is the planner's expected wall duration (friction and additive review
    included). File expectations are the caller's independent scope estimate;
    absence stays unknown. Tokens default to the request's prior or unavailable;
    a caller passes a measured forecast explicitly. Subscription points stay
    absent unless the caller passes the block derived from those tokens. This
    does not create ids or persist an artifact.
    """
    if len(report.tasks) != 1:
        raise ValueError("a single-task request requires a single-task report")
    return ForecastRecord(
        schema_version="agent-estimate/forecast/v1",
        request=request,
        created_at_utc=created_at_utc,
        engine={
            "version": report.engine_version,
            "registry_version": report.registry_version,
        },
        expected_minutes=report.timeline.expected_case_minutes,
        expected_files_touched=request.task_spec.scope.expected_files_touched,
        expected_review_minutes=report.tasks[0].review_overhead_minutes,
        basis="expected-wall",
        source=report.source,
        as_of=report.as_of,
        tokens=tokens if tokens is not None else (request.token_prior or TokenForecast()),
        subscription=subscription,
    )


def score_forecast(forecast: ForecastRecord, actual_wall_minutes: float) -> float:
    """Score matching wall time against expected minutes, never a declared cap."""
    if forecast.expected_minutes is None:
        raise ValueError("expected_minutes is required for scoring; declared caps cannot be scored")
    if isinstance(actual_wall_minutes, bool) or not isinstance(actual_wall_minutes, (int, float)):
        raise TypeError("actual_wall_minutes must be finite and non-negative")
    try:
        actual = float(actual_wall_minutes)
    except OverflowError as exc:
        raise ValueError("actual_wall_minutes must be finite and non-negative") from exc
    if not math.isfinite(actual) or actual < 0:
        raise ValueError("actual_wall_minutes must be finite and non-negative")
    ratio = actual / forecast.expected_minutes
    if not math.isfinite(ratio):
        raise ValueError("scoring ratio must be finite")
    return ratio


def resolve_scoring_basis(raw: Mapping[Any, Any]) -> tuple[float, str]:
    """Resolve observation input; cap-only and cap-derived rows are excluded.

    Legacy ``estimated_minutes`` means expected *work* minutes only. It remains
    an explicit compatibility input for the v1 work-only calibration store.
    New ``expected_minutes`` defaults to expected wall and requires total actual
    time. A cap plus a legacy alias is ambiguous and requires an independent
    expected_minutes field. No headroom divisor is guessed.
    """
    basis = raw.get("basis")
    if basis in ("declared-cap", "cap", "cap-derived"):
        raise ValueError("cap-based observations are excluded; declared caps cannot be scored")
    if "expected_minutes" in raw:
        basis = "expected-wall" if basis is None else basis
        field = "expected_minutes"
    else:
        if "declared_cap_minutes" in raw or "admission" in raw:
            raise ValueError("expected_minutes is required; declared caps cannot be scored")
        field = "estimated_minutes"
        basis = "expected-work" if basis is None else basis
        if basis != "expected-work":
            raise ValueError("legacy estimated_minutes only supports expected-work basis")
    if basis not in ("expected-wall", "expected-work"):
        raise ValueError("basis must be expected-wall or expected-work")
    value = raw[field]
    if isinstance(value, bool):
        raise TypeError(f"{field} must be numeric")
    try:
        minutes = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(minutes):
        raise ValueError(f"{field} must be finite")
    if minutes <= 0:
        raise ValueError(f"{field} must be > 0")
    if "expected_minutes" in raw and "estimated_minutes" in raw:
        raise ValueError("supply expected_minutes or legacy estimated_minutes, not both")
    return minutes, basis
