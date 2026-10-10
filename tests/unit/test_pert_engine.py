"""Tests for PERT engine, sizing, modifiers, human comparison, and METR checker."""

from __future__ import annotations

import math

import pytest

from agent_estimate.core.human_comparison import compute_human_equivalent, get_human_multiplier
from agent_estimate.core.models import (
    EstimationCategory,
    MetrWarning,
    ReviewMode,
    SizeTier,
    SizingResult,
    TaskEstimate,
    TaskType,
)
from agent_estimate.core.modifiers import (
    apply_modifiers,
    build_modifier_set,
    compute_review_overhead,
)
from agent_estimate.core.pert import (
    _MODEL_KEY_ALIASES,
    check_metr_threshold,
    compute_pert,
    estimate_task,
    load_metr_thresholds,
)
from agent_estimate.core.sizing import TIER_BASELINES, classify_task

# ---------------------------------------------------------------------------
# compute_pert
# ---------------------------------------------------------------------------


class TestComputePert:
    def test_basic_pert_formula(self) -> None:
        result = compute_pert(10, 20, 30)
        assert result.expected == pytest.approx(20.0)
        assert result.sigma == pytest.approx(10 / 3)

    def test_equal_values(self) -> None:
        result = compute_pert(15, 15, 15)
        assert result.expected == pytest.approx(15.0)
        assert result.sigma == pytest.approx(0.0)

    def test_skewed_distribution(self) -> None:
        result = compute_pert(5, 10, 50)
        expected = (5 + 40 + 50) / 6
        assert result.expected == pytest.approx(expected)
        assert result.sigma == pytest.approx(45 / 6)

    def test_invalid_order_raises(self) -> None:
        with pytest.raises(ValueError, match="O <= M <= P"):
            compute_pert(30, 20, 10)

    def test_optimistic_exceeds_most_likely_raises(self) -> None:
        with pytest.raises(ValueError, match="O <= M <= P"):
            compute_pert(25, 20, 30)

    def test_negative_optimistic_raises(self) -> None:
        with pytest.raises(ValueError, match="O >= 0"):
            compute_pert(-10, 5, 10)

    def test_result_is_frozen(self) -> None:
        result = compute_pert(10, 20, 30)
        with pytest.raises(AttributeError):
            result.expected = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# classify_task (sizing)
# ---------------------------------------------------------------------------


class TestClassifyTask:
    def test_trivial_keyword_gives_xs(self) -> None:
        result = classify_task("Fix a typo in the README")
        assert result.tier == SizeTier.XS
        assert "trivial-keyword" in result.signals

    def test_small_keyword_gives_s(self) -> None:
        result = classify_task("Simple change to add a constant")
        assert result.tier == SizeTier.S

    def test_large_keyword_gives_l(self) -> None:
        result = classify_task("Complex multi-file refactoring")
        assert result.tier == SizeTier.L

    def test_epic_keyword_gives_xl(self) -> None:
        result = classify_task("Massive rewrite of the entire auth system")
        assert result.tier == SizeTier.XL

    def test_empty_description_defaults_to_m(self) -> None:
        result = classify_task("")
        assert result.tier == SizeTier.M
        assert result.task_type == TaskType.UNKNOWN

    def test_no_signals_defaults_to_m(self) -> None:
        result = classify_task("do the thing with the stuff")
        assert result.tier == SizeTier.M
        assert "no-size-signals-default-M" in result.signals

    def test_complexity_signals_bump_tier(self) -> None:
        # "simple" gives S, but database + security = 2 complexity signals → bump by 1
        result = classify_task("Simple database migration with security tokens")
        assert result.tier == SizeTier.M  # S bumped to M

    def test_task_type_detection_bug_fix(self) -> None:
        result = classify_task("Fix a regression in the login flow")
        assert result.task_type == TaskType.BUG_FIX

    def test_task_type_detection_feature(self) -> None:
        result = classify_task("Implement a new caching layer")
        assert result.task_type == TaskType.FEATURE

    def test_task_type_detection_docs(self) -> None:
        result = classify_task("Improve the README and changelog")
        assert result.task_type == TaskType.DOCS

    def test_baselines_match_tier(self) -> None:
        result = classify_task("A trivial rename")
        o, m, p = TIER_BASELINES[SizeTier.XS]
        assert result.baseline_optimistic == pytest.approx(o)
        assert result.baseline_most_likely == pytest.approx(m)
        assert result.baseline_pessimistic == pytest.approx(p)

    def test_result_is_frozen(self) -> None:
        result = classify_task("A small fix")
        with pytest.raises(AttributeError):
            result.tier = SizeTier.XL  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_modifier_set / apply_modifiers
# ---------------------------------------------------------------------------


class TestModifiers:
    def test_default_modifiers_are_neutral(self) -> None:
        mods = build_modifier_set()
        assert mods.combined == pytest.approx(1.0)

    def test_combined_is_product(self) -> None:
        mods = build_modifier_set(spec_clarity=1.2, warm_context=1.1, agent_fit=1.1)
        expected = 1.2 * 1.1 * 1.1
        assert mods.combined == pytest.approx(expected)

    def test_apply_modifiers_scales_base(self) -> None:
        mods = build_modifier_set(spec_clarity=1.2)
        assert apply_modifiers(100.0, mods) == pytest.approx(120.0)

    def test_spec_clarity_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="spec_clarity"):
            build_modifier_set(spec_clarity=0.29)

    def test_warm_context_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="warm_context"):
            build_modifier_set(warm_context=2.0)

    def test_agent_fit_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_fit"):
            build_modifier_set(agent_fit=0.5)

    def test_modifier_set_is_frozen(self) -> None:
        mods = build_modifier_set()
        with pytest.raises(AttributeError):
            mods.combined = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute_review_overhead
# ---------------------------------------------------------------------------


class TestReviewOverhead:
    def test_none_is_zero(self) -> None:
        assert compute_review_overhead(ReviewMode.NONE) == pytest.approx(0.0)

    def test_standard_is_fifteen(self) -> None:
        assert compute_review_overhead(ReviewMode.STANDARD) == pytest.approx(15.0)

    def test_complex_is_twenty_five(self) -> None:
        assert compute_review_overhead(ReviewMode.COMPLEX) == pytest.approx(25.0)

    def test_three_round_is_thirty_five(self) -> None:
        assert compute_review_overhead(ReviewMode.THREE_ROUND) == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# human_comparison
# ---------------------------------------------------------------------------


class TestHumanComparison:
    def test_boilerplate_multiplier(self) -> None:
        mult = get_human_multiplier(TaskType.BOILERPLATE)
        assert mult == pytest.approx(math.sqrt(3.0 * 5.0))

    def test_bug_fix_multiplier(self) -> None:
        mult = get_human_multiplier(TaskType.BUG_FIX)
        assert mult == pytest.approx(math.sqrt(1.5 * 3.0))

    def test_compute_human_equivalent(self) -> None:
        agent_minutes = 30.0
        human = compute_human_equivalent(agent_minutes, TaskType.FEATURE)
        expected_mult = math.sqrt(2.0 * 4.0)
        assert human == pytest.approx(agent_minutes * expected_mult)

    def test_unknown_type_has_multiplier(self) -> None:
        mult = get_human_multiplier(TaskType.UNKNOWN)
        assert mult > 1.0


# ---------------------------------------------------------------------------
# METR thresholds
# ---------------------------------------------------------------------------


class TestMetrThresholds:
    def test_load_metr_thresholds_returns_dict(self) -> None:
        thresholds = load_metr_thresholds()
        assert isinstance(thresholds, dict)
        assert "opus" in thresholds
        assert thresholds["opus"] == pytest.approx(90.0)

    def test_check_within_threshold_returns_none(self) -> None:
        thresholds = {"opus": 90.0}
        result = check_metr_threshold("opus", 50.0, thresholds=thresholds)
        assert result is None

    def test_check_exceeds_threshold_returns_warning(self) -> None:
        thresholds = {"opus": 90.0}
        result = check_metr_threshold("opus", 120.0, thresholds=thresholds)
        assert result is not None
        assert isinstance(result, MetrWarning)
        assert result.model_key == "opus"
        assert result.threshold_minutes == pytest.approx(90.0)
        assert result.estimated_minutes == pytest.approx(120.0)
        assert "exceeds" in result.message

    @pytest.mark.parametrize(
        ("model_key", "expected_model_key", "expected_threshold"),
        [
            ("claude", "opus_5_5", 90.0),
            ("opus_4_6", "opus_4_6", 90.0),
            ("codex", "gpt_6_astra", 90.0),
            ("production", "gpt_6_sol", 60.0),
            ("gemini", "gemini_3_1_pro", 45.0),
            ("gpt-5.3", "gpt_5_3", 60.0),
        ],
    )
    def test_alias_model_key_resolves_to_threshold_key(
        self, model_key: str, expected_model_key: str, expected_threshold: float
    ) -> None:
        thresholds = {
            "opus_5_5": 90.0,
            "opus_4_6": 90.0,
            "gpt_6_astra": 90.0,
            "gpt_6_sol": 60.0,
            "gemini_3_1_pro": 45.0,
            "gpt_5_3": 60.0,
        }
        result = check_metr_threshold(
            model_key, expected_threshold + 1.0, thresholds=thresholds, fallback_threshold=30.0
        )
        assert result is not None
        assert result.model_key == expected_model_key
        assert result.threshold_minutes == pytest.approx(expected_threshold)

    @pytest.mark.parametrize(
        ("model_id", "expected_model_key", "expected_threshold"),
        [
            ("claude-fable-5-1", "fable_5_1", 90.0),
            ("claude-opus-5-5", "opus_5_5", 90.0),
            ("claude-sonnet-5-5", "sonnet_5_5", 30.0),
            ("claude-haiku-4-5", "haiku_4_5", 15.0),
            ("claude-haiku-4-5-20251001", "haiku_4_5", 15.0),
            ("gpt-6-astra", "gpt_6_astra", 90.0),
            ("gpt-6-sol", "gpt_6_sol", 60.0),
            ("gpt-6-luna", "gpt_6_luna", 15.0),
        ],
    )
    def test_vendor_model_id_resolves_to_shipped_row(
        self, model_id: str, expected_model_key: str, expected_threshold: float
    ) -> None:
        result = check_metr_threshold(model_id, expected_threshold + 1.0, fallback_threshold=None)
        assert result is not None
        assert result.model_key == expected_model_key
        assert result.threshold_minutes == pytest.approx(expected_threshold)
        assert "local reliability policy (unmeasured)" in result.message

    def test_every_alias_target_has_a_shipped_row(self) -> None:
        thresholds = load_metr_thresholds()
        assert sorted(set(_MODEL_KEY_ALIASES.values()) - set(thresholds)) == []

    def test_frontier_model_tier_resolves_by_assigned_agent(self) -> None:
        thresholds = {
            "opus_5_5": 90.0,
            "opus_4_6": 90.0,
            "gpt_6_astra": 90.0,
            "gpt_6_sol": 60.0,
            "gemini_3_1_pro": 45.0,
        }
        claude_result = check_metr_threshold(
            "frontier",
            70.0,
            thresholds=thresholds,
            fallback_threshold=45.0,
            agent_name="Claude",
        )
        gemini_result = check_metr_threshold(
            "frontier",
            70.0,
            thresholds=thresholds,
            fallback_threshold=45.0,
            agent_name="Gemini",
        )
        codex_result = check_metr_threshold(
            "frontier",
            95.0,
            thresholds=thresholds,
            fallback_threshold=45.0,
            agent_name="Codex",
        )
        assert claude_result is None  # 70 < 90 opus threshold
        assert codex_result is not None
        assert codex_result.model_key == "gpt_6_astra"
        assert codex_result.threshold_minutes == pytest.approx(90.0)
        assert gemini_result is not None
        assert gemini_result.model_key == "gemini_3_1_pro"
        assert gemini_result.threshold_minutes == pytest.approx(45.0)

    def test_unknown_model_logs_warning_when_falling_back(self, caplog: pytest.LogCaptureFixture) -> None:
        thresholds = {"opus": 90.0}
        with caplog.at_level("WARNING", logger="agent_estimate"):
            result = check_metr_threshold(
                "mystery-model",
                50.0,
                thresholds=thresholds,
                fallback_threshold=40.0,
            )
        assert result is not None
        assert result.threshold_minutes == pytest.approx(40.0)
        assert "METR threshold not found" in caplog.text
        assert "mystery-model" in caplog.text

    def test_unknown_model_uses_fallback(self) -> None:
        thresholds = {"opus": 90.0}
        result = check_metr_threshold(
            "unknown_model", 50.0, thresholds=thresholds, fallback_threshold=40.0
        )
        assert result is not None
        assert result.threshold_minutes == pytest.approx(40.0)

    def test_fractional_fallback_threshold_preserves_precision(self) -> None:
        result = check_metr_threshold(
            "unknown_model",
            50.0,
            thresholds={},
            fallback_threshold=40.25,
        )

        assert result is not None
        assert "(40.25m)" in result.message

    def test_at_threshold_returns_none(self) -> None:
        thresholds = {"opus": 90.0}
        result = check_metr_threshold("opus", 90.0, thresholds=thresholds)
        assert result is None


# ---------------------------------------------------------------------------
# estimate_task (full pipeline)
# ---------------------------------------------------------------------------


class TestEstimateTask:
    def _make_sizing(self, tier: SizeTier = SizeTier.S) -> SizingResult:
        o, m, p = TIER_BASELINES[tier]
        return SizingResult(
            tier=tier,
            baseline_optimistic=o,
            baseline_most_likely=m,
            baseline_pessimistic=p,
            task_type=TaskType.FEATURE,
            signals=("test",),
        )

    def test_basic_pipeline(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set()
        thresholds = {"opus": 90.0}

        result = estimate_task(
            sizing, mods, model_key="opus", thresholds=thresholds
        )

        assert isinstance(result, TaskEstimate)
        assert result.estimation_category == EstimationCategory.CODING
        # PERT E = (O + 4M + P) / 6 = (12 + 4*23 + 40) / 6 = 144 / 6 = 24.0
        o, m, p = TIER_BASELINES[SizeTier.S]
        expected_pert = (o + 4 * m + p) / 6
        assert result.pert.expected == pytest.approx(expected_pert)
        assert result.review_minutes == pytest.approx(0.0)
        assert result.total_expected_minutes == pytest.approx(result.pert.expected)

    def test_with_review_overhead_standard(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set()
        thresholds = {"opus": 90.0}

        result = estimate_task(
            sizing, mods, review_mode=ReviewMode.STANDARD, thresholds=thresholds
        )

        assert result.review_minutes == pytest.approx(15.0)
        assert result.total_expected_minutes == pytest.approx(result.pert.expected + 15.0)

    def test_with_review_overhead_complex(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set()
        thresholds = {"opus": 90.0}

        result = estimate_task(
            sizing, mods, review_mode=ReviewMode.COMPLEX, thresholds=thresholds
        )

        assert result.review_minutes == pytest.approx(25.0)
        assert result.total_expected_minutes == pytest.approx(result.pert.expected + 25.0)

    def test_with_modifiers(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set(spec_clarity=1.2)
        thresholds = {"opus": 200.0}

        result = estimate_task(sizing, mods, thresholds=thresholds)

        # Baselines scaled by 1.2
        expected_o = sizing.baseline_optimistic * 1.2
        expected_m = sizing.baseline_most_likely * 1.2
        expected_p = sizing.baseline_pessimistic * 1.2
        expected_pert = (expected_o + 4 * expected_m + expected_p) / 6

        assert result.pert.expected == pytest.approx(expected_pert)

    def test_metr_warning_when_exceeds(self) -> None:
        sizing = self._make_sizing(SizeTier.XL)
        mods = build_modifier_set()
        thresholds = {"opus": 90.0}

        result = estimate_task(sizing, mods, model_key="opus", thresholds=thresholds)

        assert result.metr_warning is not None
        assert result.metr_warning.model_key == "opus"

    def test_no_metr_warning_when_within(self) -> None:
        sizing = self._make_sizing(SizeTier.XS)
        mods = build_modifier_set()
        thresholds = {"opus": 90.0}

        result = estimate_task(sizing, mods, model_key="opus", thresholds=thresholds)

        assert result.metr_warning is None

    def test_human_equivalent_passthrough(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set()
        thresholds = {"opus": 200.0}

        result = estimate_task(
            sizing, mods, thresholds=thresholds, human_equivalent_minutes=120.0
        )

        assert result.human_equivalent_minutes == pytest.approx(120.0)

    def test_result_is_frozen(self) -> None:
        sizing = self._make_sizing()
        mods = build_modifier_set()
        thresholds = {"opus": 200.0}

        result = estimate_task(sizing, mods, thresholds=thresholds)

        with pytest.raises(AttributeError):
            result.total_expected_minutes = 0  # type: ignore[misc]
