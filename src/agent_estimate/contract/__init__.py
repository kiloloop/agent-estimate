"""Versioned forecast contracts, independent of the legacy estimation pipeline."""

from agent_estimate.contract.binding import (
    BindingReceipt,
    ReceiptError,
    forecast_key,
    forecast_sha256,
    read_binding_receipt,
    receipt_path,
    write_binding_receipt,
)
from agent_estimate.contract.schema import (
    AdmissionEnvelope,
    EstimateRequest,
    ExecutionProfile,
    ForecastRecord,
    OutcomeObservation,
    TaskSpec,
)
from agent_estimate.contract.subscription import MeterTable, subscription_forecast
from agent_estimate.contract.tokens import (
    TokenObservation,
    TokenObservations,
    fit_segments,
    measured_token_forecast,
)

__all__ = [
    "AdmissionEnvelope",
    "BindingReceipt",
    "EstimateRequest",
    "ExecutionProfile",
    "ForecastRecord",
    "MeterTable",
    "OutcomeObservation",
    "ReceiptError",
    "TaskSpec",
    "TokenObservation",
    "TokenObservations",
    "fit_segments",
    "forecast_key",
    "forecast_sha256",
    "measured_token_forecast",
    "read_binding_receipt",
    "receipt_path",
    "subscription_forecast",
    "write_binding_receipt",
]
