"""Measured token correction from caller-supplied observed tokens, per segment.

The package ships no token numbers. A caller supplies the observed tokens of its
own closed legs, each keyed to a task-type × execution-profile segment and,
where a binding receipt exists, to the forecast it executed. This module fits
one shrunk log-median per segment and labels the result ``basis: measured``
only when the segment reaches the minimum sample size. Below it, the slots stay
``unavailable`` or keep the caller's supplied prior. Runtime logs are never read
here; producing the observation file is the caller's join.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from agent_estimate.contract.schema import (
    MINIMUM_SEGMENT_N,
    ContractModel,
    Count,
    EstimateRequest,
    Identifier,
    NonEmptyStr,
    ObservationWindow,
    TaskKind,
    TokenForecast,
    TokenSegment,
)

#: Weight of a caller-supplied prior when a measured segment is shrunk toward it,
#: expressed as pseudo-observations. Equal to the qualification bar, so a segment
#: exactly at the bar and the prior carry the same weight.
PRIOR_PSEUDO_OBSERVATIONS = 5

TOKEN_SHRINKAGE_NOTE = (
    "Shrinkage note: measured counts are pulled toward the caller-supplied local-policy "
    "prior with five pseudo-observations; that prior is not calibrated for this segment."
)

PositiveCount = Annotated[int, Field(strict=True, ge=1)]


class TokenObservation(ContractModel):
    """Observed tokens of one closed leg, keyed to its segment and, if bound, its forecast.

    Total means processed tokens including cache carry; output and cache-read
    tokens are each included in total. Counts describe a whole leg and are never
    divided into smaller tasks.
    """

    task_type: TaskKind
    execution_profile_id: Identifier
    tokens_total: PositiveCount
    tokens_output: PositiveCount
    tokens_cache_read: Count | None = None
    forecast_key: NonEmptyStr | None = None
    execution_id: Identifier | None = None

    @model_validator(mode="after")
    def require_consistent_counts(self) -> TokenObservation:
        if self.tokens_output > self.tokens_total:
            raise ValueError("observed output tokens cannot exceed total processed tokens")
        if self.tokens_cache_read is not None and self.tokens_cache_read > self.tokens_total:
            raise ValueError("observed cache-read tokens cannot exceed total processed tokens")
        return self


class TokenObservations(ContractModel):
    """The caller's token-per-leg join: its provenance, window and observed legs."""

    schema_version: Literal["agent-estimate/token-observations/v1"]
    source: NonEmptyStr
    window: ObservationWindow
    observations: tuple[TokenObservation, ...] = ()

    @field_validator("observations")
    @classmethod
    def reject_duplicate_executions(
        cls, values: tuple[TokenObservation, ...]
    ) -> tuple[TokenObservation, ...]:
        """One closed leg is one observation; a repeated execution id is ambiguous."""
        executions = [row.execution_id for row in values if row.execution_id is not None]
        if len(executions) != len(set(executions)):
            raise ValueError("execution ids must be unique")
        return values


@dataclass(frozen=True)
class SegmentFit:
    """Measured counts of one qualifying segment, reproducible from its observations."""

    task_type: str
    execution_profile_id: str
    n: int
    expected_tokens_total: int
    expected_tokens_output: int
    expected_tokens_cache_read: int | None
    cache_read_n: int
    shrunk_toward_prior: bool


def log_median(values: Sequence[int]) -> float:
    """Median of the natural logs; an even count takes the middle pair's mean."""
    if not values:
        raise ValueError("log median requires at least one value")
    logs = sorted(math.log(value) for value in values)
    upper = logs[len(logs) // 2]
    if len(logs) % 2:
        return upper
    lower = logs[len(logs) // 2 - 1]
    return lower + (upper - lower) / 2


def shrunk_log_median(values: Sequence[int], *, center: float | None) -> float:
    """Log-median pulled toward ``center`` with ``PRIOR_PSEUDO_OBSERVATIONS`` weight.

    Without a center the log-median stands as measured. With one, the segment's
    ``n`` observations and the center's pseudo-observations are averaged in log
    space, so a segment at the bar sits halfway and trust grows with ``n``.
    """
    median = log_median(values)
    if center is None:
        return median
    n = len(values)
    return (n * median + PRIOR_PSEUDO_OBSERVATIONS * center) / (n + PRIOR_PSEUDO_OBSERVATIONS)


def _prior_center(count: int | None) -> float | None:
    # A zero prior has no log and carries no pull.
    return math.log(count) if count else None


def fit_segment(rows: Sequence[TokenObservation], prior: TokenForecast | None = None) -> SegmentFit | None:
    """Fit one segment; ``None`` below the minimum sample size.

    Total and output are shrunk log-medians of the observed counts. The cache-read
    slot is the median observed cache-read share applied to the fitted total, and
    it needs its own ``MINIMUM_SEGMENT_N`` legs carrying a cache-read count. Each
    slot is shrunk toward the prior only when the prior supplies that slot.
    """
    if len(rows) < MINIMUM_SEGMENT_N:
        return None
    if len({(row.task_type, row.execution_profile_id) for row in rows}) != 1:
        raise ValueError("a segment fit takes observations of exactly one segment")
    prior_total = prior.expected_tokens_total if prior is not None else None
    prior_output = prior.expected_tokens_output if prior is not None else None
    prior_cache_read = prior.expected_tokens_cache_read if prior is not None else None
    total_center, output_center = _prior_center(prior_total), _prior_center(prior_output)
    total = round(math.exp(shrunk_log_median(
        [row.tokens_total for row in rows], center=total_center,
    )))
    output = round(math.exp(shrunk_log_median(
        [row.tokens_output for row in rows], center=output_center,
    )))
    # Output never exceeds total per observation, so the medians keep that order;
    # only an asymmetric prior pull can invert it, and the contract forbids that.
    output = min(output, total)
    shrunk = total_center is not None or output_center is not None
    cache_rows = [row for row in rows if row.tokens_cache_read is not None]
    cache_read = None
    if len(cache_rows) >= MINIMUM_SEGMENT_N:
        shares = sorted(row.tokens_cache_read / row.tokens_total for row in cache_rows)
        share = _median(shares)
        if prior_total and prior_cache_read is not None:
            prior_share = prior_cache_read / prior_total
            n = len(shares)
            share = (n * share + PRIOR_PSEUDO_OBSERVATIONS * prior_share) / (
                n + PRIOR_PSEUDO_OBSERVATIONS
            )
            shrunk = True
        cache_read = min(round(share * total), total)
    return SegmentFit(
        task_type=rows[0].task_type,
        execution_profile_id=rows[0].execution_profile_id,
        n=len(rows),
        expected_tokens_total=total,
        expected_tokens_output=output,
        expected_tokens_cache_read=cache_read,
        cache_read_n=len(cache_rows),
        shrunk_toward_prior=shrunk,
    )


def _median(values: Sequence[float]) -> float:
    upper = values[len(values) // 2]
    if len(values) % 2:
        return upper
    lower = values[len(values) // 2 - 1]
    return lower + (upper - lower) / 2


def fit_segments(observations: TokenObservations) -> dict[tuple[str, str], SegmentFit]:
    """Fit every qualifying segment without a prior; keys are (task_type, profile id)."""
    segments: dict[tuple[str, str], list[TokenObservation]] = defaultdict(list)
    for row in observations.observations:
        segments[(row.task_type, row.execution_profile_id)].append(row)
    fits = {}
    for key in sorted(segments):
        fit = fit_segment(segments[key])
        if fit is not None:
            fits[key] = fit
    return fits


def segment_of(request: EstimateRequest) -> tuple[str, str]:
    """The segment a request belongs to: its task type and execution profile id."""
    return request.task_spec.task_type, request.execution_profile.execution_profile_id


def measured_token_forecast(
    request: EstimateRequest, observations: TokenObservations
) -> TokenForecast:
    """Token forecast for the request's segment: measured at the bar, else prior or unavailable.

    The execution profile id is the segment key, so a profile whose settings
    change needs a new id or its observations pool across the change.
    """
    key = segment_of(request)
    rows = [
        row for row in observations.observations
        if (row.task_type, row.execution_profile_id) == key
    ]
    prior = request.token_prior
    fit = fit_segment(rows, prior)
    if fit is None:
        return prior or TokenForecast()
    window = observations.window
    return TokenForecast(
        expected_tokens_total=fit.expected_tokens_total,
        expected_tokens_output=fit.expected_tokens_output,
        expected_tokens_cache_read=fit.expected_tokens_cache_read,
        basis="measured",
        source=observations.source,
        as_of=window.end,
        population=(
            f"{key[0]} × {key[1]}: n={fit.n} observed legs, "
            f"window {window.start.isoformat()}..{window.end.isoformat()}"
        ),
        warnings=(TOKEN_SHRINKAGE_NOTE,) if fit.shrunk_toward_prior else (),
        segment=TokenSegment(task_type=key[0], execution_profile_id=key[1], n=fit.n),
        window=window,
    )
