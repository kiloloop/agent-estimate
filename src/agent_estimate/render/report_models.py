"""Shared report data models for renderers."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass

from agent_estimate.contract.schema import SubscriptionForecast, TokenForecast
from agent_estimate.core.models import EstimationCategory, TaskEstimate
from agent_estimate.version import __version__


@dataclass(frozen=True)
class ReportTask:
    """Flattened per-task report row for renderers."""

    name: str
    tier: str
    agent: str
    base_pert_optimistic_minutes: float
    base_pert_most_likely_minutes: float
    base_pert_pessimistic_minutes: float
    modifier_spec_clarity: float
    modifier_warm_context: float
    modifier_agent_fit: float
    modifier_combined: float
    modifier_raw_combined: float
    modifier_clamped: bool
    effective_duration_minutes: float
    human_equivalent_minutes: float | None
    review_overhead_minutes: float
    metr_warning: str | None = None
    warm_context_detail: str | None = None
    tier_correction_warnings: tuple[str, ...] = ()
    estimation_category: EstimationCategory | None = None
    estimate_factor: float = 1.0
    pre_adjustment_minutes: float | None = None

    @property
    def work_before_adjustment_minutes(self) -> float:
        """Input to the applied profile hook; review and friction are separate."""
        if self.pre_adjustment_minutes is not None:
            return self.pre_adjustment_minutes
        return self.effective_duration_minutes

    @property
    def base_pert_expected_minutes(self) -> float:
        """Return expected minutes for the unmodified base PERT tuple."""
        return (
            self.base_pert_optimistic_minutes
            + (4 * self.base_pert_most_likely_minutes)
            + self.base_pert_pessimistic_minutes
        ) / 6

    @classmethod
    def from_estimate(cls, *, name: str, agent: str, estimate: TaskEstimate) -> ReportTask:
        """Construct a report row from a TaskEstimate."""
        warning = estimate.metr_warning.message if estimate.metr_warning is not None else None
        return cls(
            name=name,
            tier=estimate.sizing.tier.value,
            agent=agent,
            base_pert_optimistic_minutes=estimate.sizing.baseline_optimistic,
            base_pert_most_likely_minutes=estimate.sizing.baseline_most_likely,
            base_pert_pessimistic_minutes=estimate.sizing.baseline_pessimistic,
            modifier_spec_clarity=estimate.modifiers.spec_clarity,
            modifier_warm_context=estimate.modifiers.warm_context,
            modifier_agent_fit=estimate.modifiers.agent_fit,
            modifier_combined=estimate.modifiers.combined,
            modifier_raw_combined=estimate.modifiers.raw_combined,
            modifier_clamped=estimate.modifiers.clamped,
            effective_duration_minutes=estimate.pert.expected,
            human_equivalent_minutes=estimate.human_equivalent_minutes,
            review_overhead_minutes=estimate.review_minutes,
            metr_warning=warning,
            estimate_factor=estimate.estimate_factor,
            pre_adjustment_minutes=estimate.pre_adjustment_minutes,
        )


@dataclass(frozen=True)
class ReportWave:
    """One scheduling wave in the report.

    ``agent_review_minutes`` maps each agent name to the single amortized
    review cycle charged for that agent in this wave.  Empty when all tasks
    have review_minutes=0 (i.e. ReviewMode.NONE).
    """

    number: int
    tasks: tuple[str, ...]
    duration_minutes: float
    agent_assignments: Mapping[str, tuple[str, ...]]
    agent_review_minutes: Mapping[str, float] = dataclasses.field(default_factory=dict)  # type: ignore[assignment]


@dataclass(frozen=True)
class ReportTimeline:
    """Top-level timeline summary metrics."""

    best_case_minutes: float
    expected_case_minutes: float
    worst_case_minutes: float
    human_equivalent_minutes: float

    @property
    def compression_ratio(self) -> float:
        """Return human/agent ratio. Returns 0 when expected time is 0."""
        if self.expected_case_minutes <= 0:
            return 0.0
        return self.human_equivalent_minutes / self.expected_case_minutes


@dataclass(frozen=True)
class ReportAgentLoad:
    """Agent-level load and cost totals."""

    agent: str
    task_count: int
    total_work_minutes: float
    estimated_cost: float

    @property
    def heuristic_cost(self) -> float:
        """Return the five-minute-turn cost heuristic under its explicit name."""
        return self.estimated_cost


@dataclass(frozen=True)
class EstimationReport:
    """Renderer input bundle for a full estimate report."""

    tasks: tuple[ReportTask, ...]
    waves: tuple[ReportWave, ...]
    timeline: ReportTimeline
    agent_load: tuple[ReportAgentLoad, ...]
    critical_path: tuple[str, ...]
    title: str = "Agent Estimate Report"
    engine_version: str = __version__
    registry_version: str = "unversioned"
    schema_version: str | None = None
    basis: str | None = None
    source: str | None = None
    as_of: str | None = None
    tokens: TokenForecast | None = None
    subscription: SubscriptionForecast | None = None

    @property
    def review_overhead_minutes(self) -> float:
        """Sum additive review overhead across all tasks."""
        return sum(task.review_overhead_minutes for task in self.tasks)
