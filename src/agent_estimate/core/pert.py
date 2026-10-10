"""PERT estimation calculations for AI agent tasks."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import as_file, files

import yaml

from agent_estimate.core.models import (
    EstimationCategory,
    MetrWarning,
    ModifierSet,
    PertResult,
    ReviewMode,
    SizingResult,
    TaskEstimate,
)
from agent_estimate.core.modifiers import apply_modifiers, compute_review_overhead

METR_THRESHOLDS_FILENAME = "metr_thresholds.yaml"
logger = logging.getLogger("agent_estimate")

_ALLOWED_THRESHOLD_BASES = frozenset({"measured", "extrapolated", "local-policy"})


@dataclass(frozen=True)
class _ThresholdProvenance:
    """Provenance attached to one backwards-compatible numeric threshold."""

    basis: str
    source: str
    source_version: str
    as_of: str


class _ThresholdRegistry(dict[str, float]):
    """Numeric threshold mapping with non-schema provenance metadata."""

    def __init__(
        self,
        values: Mapping[str, float],
        provenance: Mapping[str, _ThresholdProvenance],
        registry_version: str,
    ) -> None:
        super().__init__(values)
        self.provenance = dict(provenance)
        self.registry_version = registry_version

_MODEL_KEY_ALIASES: dict[str, str] = {
    # Current fleet (2026-09)
    "fable_5_1": "fable_5_1",
    "fable": "fable_5_1",
    "claude_fable": "fable_5_1",
    "opus_5_5": "opus_5_5",
    "claude": "opus_5_5",
    "claude_opus": "opus_5_5",
    "gpt_6_astra": "gpt_6_astra",
    "codex": "gpt_6_astra",
    "codex_latest": "gpt_6_astra",
    "gpt_6_sol": "gpt_6_sol",
    "production": "gpt_6_sol",
    "gemini_3_1_pro": "gemini_3_1_pro",
    "gemini": "gemini_3_1_pro",
    "gemini_pro": "gemini_3_1_pro",
    "sonnet_5_5": "sonnet_5_5",
    "sonnet": "sonnet_5_5",
    "gpt_6_luna": "gpt_6_luna",
    "haiku_4_5": "haiku_4_5",
    "haiku": "haiku_4_5",
    # Vendor model ids, as a typed request's execution_profile.model.id carries them
    "claude_fable_5_1": "fable_5_1",
    "claude_opus_5_5": "opus_5_5",
    "claude_sonnet_5_5": "sonnet_5_5",
    "claude_haiku_4_5": "haiku_4_5",
    "claude_haiku_4_5_20251001": "haiku_4_5",
    # Earlier models
    "opus_4_x": "opus_4_x",
    "opus_4_7": "opus_4_7",
    "opus_4_6": "opus_4_6",
    "gpt_5_5": "gpt_5_5",
    "gpt_5_4": "gpt_5_4",
    "sonnet_4_6": "sonnet_4_6",
    "opus": "opus",
    "gpt_5_3": "gpt_5_3",
    "gpt_5_2": "gpt_5_2",
    "gpt_5": "gpt_5",
    "gemini_3_pro": "gemini_3_pro",
}


def _normalize_model_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def _resolve_threshold_model_key(model_key: str, *, agent_name: str | None = None) -> str:
    normalized_model = _normalize_model_token(model_key)
    if normalized_model in _MODEL_KEY_ALIASES:
        return _MODEL_KEY_ALIASES[normalized_model]

    if normalized_model == "frontier" and agent_name:
        normalized_agent = _normalize_model_token(agent_name)
        if "claude" in normalized_agent:
            return "opus_5_5"
        if "codex" in normalized_agent:
            return "gpt_6_astra"
        if "gemini" in normalized_agent:
            return "gemini_3_1_pro"

    return normalized_model


def compute_pert(optimistic: float, most_likely: float, pessimistic: float) -> PertResult:
    """Compute PERT expected value and standard deviation.

    Formula: E = (O + 4M + P) / 6, sigma = (P - O) / 6
    """
    if optimistic < 0:
        raise ValueError(f"PERT requires O >= 0, got O={optimistic}")
    if not (optimistic <= most_likely <= pessimistic):
        raise ValueError(
            f"PERT requires O <= M <= P, got O={optimistic}, M={most_likely}, P={pessimistic}"
        )
    expected = (optimistic + 4 * most_likely + pessimistic) / 6
    sigma = (pessimistic - optimistic) / 6
    return PertResult(
        optimistic=optimistic,
        most_likely=most_likely,
        pessimistic=pessimistic,
        expected=expected,
        sigma=sigma,
    )


def load_metr_thresholds() -> dict[str, float]:
    """Load reliability thresholds from the packaged YAML file.

    The public return shape remains ``dict[model_key, minutes]`` for backwards
    compatibility. The concrete mapping also retains registry provenance for
    warning labels.
    """
    resource = files("agent_estimate").joinpath(METR_THRESHOLDS_FILENAME)
    with as_file(resource) as path:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        if not isinstance(raw, Mapping):
            raise TypeError("root must be a mapping")
        registry_version = raw["registry_version"]
        if not isinstance(registry_version, str) or not registry_version.strip():
            raise ValueError("registry_version must be a non-empty string")
        values: dict[str, float] = {}
        provenance: dict[str, _ThresholdProvenance] = {}
        for key, entry in raw.get("models", {}).items():
            basis = str(entry["basis"])
            if basis not in _ALLOWED_THRESHOLD_BASES:
                raise ValueError(f"unsupported basis {basis!r} for {key!r}")
            values[key] = float(entry["p80_minutes"])
            provenance[key] = _ThresholdProvenance(
                basis=basis,
                source=str(entry["source"]),
                source_version=str(entry["source_version"]),
                as_of=str(entry["as_of"]),
            )
        return _ThresholdRegistry(values, provenance, registry_version.strip())
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Malformed {METR_THRESHOLDS_FILENAME}: {exc}") from exc


def _basis_label(provenance: _ThresholdProvenance | None) -> str:
    if provenance is None or provenance.basis == "local-policy":
        return "local reliability policy (unmeasured)"
    if provenance.basis == "extrapolated":
        return "extrapolated reliability horizon (unmeasured)"
    return (
        "measured p80 reliability horizon "
        f"(source: {provenance.source}, {provenance.source_version}, "
        f"as of {provenance.as_of})"
    )


def _format_threshold_minutes(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def check_metr_threshold(
    model_key: str,
    estimated_minutes: float,
    *,
    thresholds: Mapping[str, float] | None = None,
    fallback_threshold: float | None = 40.0,
    agent_name: str | None = None,
) -> MetrWarning | None:
    """Check whether work exceeds a configured reliability threshold.

    The function name is retained for API compatibility. A threshold is only
    described as measured p80 evidence when its registry row supplies the
    corresponding provenance; configured and fallback values are local policy.

    Args:
        model_key: Concrete model identifier (e.g. "opus", "sonnet").
        estimated_minutes: Work-only estimated minutes for the task.
        thresholds: Optional pre-loaded thresholds dict. If None, loads from YAML.
        fallback_threshold: Local-policy fallback for an unknown model. ``None``
            records the reliability horizon as unavailable and emits no warning.
        agent_name: Optional assigned agent name for resolving legacy model tiers.

    Returns:
        A MetrWarning if the estimate exceeds the threshold, else None.
    """
    if thresholds is None:
        thresholds = load_metr_thresholds()

    resolved_model_key = _resolve_threshold_model_key(model_key, agent_name=agent_name)
    threshold = thresholds.get(resolved_model_key)
    provenance = (
        thresholds.provenance.get(resolved_model_key)
        if isinstance(thresholds, _ThresholdRegistry)
        else None
    )
    if threshold is None:
        if fallback_threshold is None:
            logger.warning(
                "Reliability horizon unavailable for model_key=%r "
                "(resolved=%r, agent_name=%r); no fallback policy configured",
                model_key,
                resolved_model_key,
                agent_name,
            )
            return None
        logger.warning(
            "METR threshold not found for model_key=%r "
            "(resolved=%r, agent_name=%r); using local reliability policy "
            "(unmeasured) %sm",
            model_key,
            resolved_model_key,
            agent_name,
            _format_threshold_minutes(fallback_threshold),
        )
        threshold = fallback_threshold

    if estimated_minutes <= threshold:
        return None

    threshold_text = _format_threshold_minutes(threshold)
    return MetrWarning(
        model_key=resolved_model_key,
        threshold_minutes=threshold,
        estimated_minutes=estimated_minutes,
        message=(
            f"Work estimate ({estimated_minutes:.1f}m) exceeds {resolved_model_key} "
            f"{_basis_label(provenance)} ({threshold_text}m). "
            "Consider splitting the task."
        ),
    )


def estimate_task(
    sizing: SizingResult,
    modifiers: ModifierSet,
    *,
    review_mode: ReviewMode = ReviewMode.NONE,
    model_key: str = "opus",
    thresholds: Mapping[str, float] | None = None,
    fallback_threshold: float = 40.0,
    agent_name: str | None = None,
    human_equivalent_minutes: float | None = None,
) -> TaskEstimate:
    """Full estimation pipeline: sizing -> PERT -> modifiers -> review -> policy check.

    Args:
        sizing: Task sizing result with calibrated baselines.
        modifiers: Modifier set to apply to baselines.
        review_mode: Code review overhead model.
        model_key: Concrete model identifier for the reliability check.
        thresholds: Pre-loaded reliability thresholds (optional).
        fallback_threshold: Local-policy fallback when model_key is unknown.
        agent_name: Optional assigned agent name for resolving legacy model tiers.
        human_equivalent_minutes: Pre-computed human equivalent (optional).

    Returns:
        A complete TaskEstimate.
    """
    # All three baselines are scaled by the same combined modifier,
    # preserving the O/P ratio intentionally. Modifier uncertainty is
    # captured by the modifier ranges themselves, not PERT spread.
    adjusted_o = apply_modifiers(sizing.baseline_optimistic, modifiers)
    adjusted_m = apply_modifiers(sizing.baseline_most_likely, modifiers)
    adjusted_p = apply_modifiers(sizing.baseline_pessimistic, modifiers)

    pert = compute_pert(adjusted_o, adjusted_m, adjusted_p)

    review_minutes = compute_review_overhead(review_mode)
    total = pert.expected + review_minutes

    metr_warning = check_metr_threshold(
        model_key,
        pert.expected,
        thresholds=thresholds,
        fallback_threshold=fallback_threshold,
        agent_name=agent_name,
    )

    return TaskEstimate(
        sizing=sizing,
        pert=pert,
        modifiers=modifiers,
        review_minutes=review_minutes,
        total_expected_minutes=total,
        human_equivalent_minutes=human_equivalent_minutes,
        metr_warning=metr_warning,
        estimation_category=EstimationCategory.CODING,
    )
