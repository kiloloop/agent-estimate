"""Experimental subscription-points forecast from a caller-supplied meter table.

The package ships no meter numbers. Subscription meters are undocumented,
seat-specific and move on provider tier steps, so a caller supplies its own
dated table and the points are that table applied to the token forecast's
split. A tier step or a changed window is expressed only by a new dated table;
nothing here scales, ages or infers one.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import field_validator

from agent_estimate.contract.schema import (
    SUBSCRIPTION_METER_WARNING,
    ContractModel,
    EstimateRequest,
    Meter,
    NonEmptyStr,
    SubscriptionForecast,
    TokenForecast,
    _calendar_date,
)


class MeterTable(ContractModel):
    """One seat's meters per model id, with the table's provenance and date."""

    schema_version: Literal["agent-estimate/meter-table/v1"]
    source: NonEmptyStr
    as_of: date
    meters: tuple[Meter, ...]

    @field_validator("as_of", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> object:
        try:
            return _calendar_date(value)
        except ValueError as exc:
            raise ValueError(f"meter table as_of {exc}") from exc

    @field_validator("meters")
    @classmethod
    def reject_duplicate_models(cls, values: tuple[Meter, ...]) -> tuple[Meter, ...]:
        """One model has one meter per table; a repeated model id is ambiguous."""
        models = [meter.model_id for meter in values]
        if len(models) != len(set(models)):
            raise ValueError("model ids must be unique")
        return values

    def meter_for(self, model_id: str) -> Meter | None:
        """The meter whose model id matches exactly; ids are never normalized."""
        return next((meter for meter in self.meters if meter.model_id == model_id), None)


def subscription_forecast(
    request: EstimateRequest, tokens: TokenForecast | None, table: MeterTable
) -> SubscriptionForecast:
    """Points for the request's assigned agent: the table's meter applied to ``tokens``.

    The meter is selected by ``execution_profile.model.id``. The block is always
    labeled: points with their meter, or ``unavailable`` with the first missing
    input named.
    """
    tokens = tokens if tokens is not None else TokenForecast()
    profile = request.execution_profile
    model_id = profile.model.id
    meter = table.meter_for(model_id) if model_id is not None else None
    total, cache_read = tokens.expected_tokens_total, tokens.expected_tokens_cache_read
    evidence = {
        "agent_name": profile.runtime.agent_name,
        "model_id": model_id,
        "token_basis": tokens.basis,
        "source": table.source,
        "as_of": table.as_of,
    }
    reason = None
    if tokens.basis == "unavailable":
        reason = "no token forecast: supply a token prior or token observations"
    elif model_id is None:
        reason = "execution_profile.model.id is unknown; a meter is selected by model id"
    elif meter is None:
        reason = f"the meter table has no meter for model id {model_id!r}"
    elif total is None:
        reason = "the token forecast has no expected total"
    elif cache_read is None:
        reason = "the token forecast has no expected cache-read count; no split is assumed"
    if reason is not None:
        return SubscriptionForecast(basis="unavailable", unavailable_reason=reason, **evidence)
    points = meter.points(total, cache_read)
    return SubscriptionForecast(
        expected_points=points,
        window_fraction=points / meter.window_points,
        basis="local-policy",
        meter=meter,
        warnings=(SUBSCRIPTION_METER_WARNING, *tokens.warnings),
        **evidence,
    )
