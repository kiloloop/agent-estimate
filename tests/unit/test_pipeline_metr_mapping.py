"""Pipeline-level tests for METR model-key mapping behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_estimate.cli.commands import _pipeline
from agent_estimate.cli.commands._pipeline import run_estimate_pipeline
from agent_estimate.core.models import (
    AgentProfile,
    EstimationCategory,
    EstimationConfig,
    ProjectSettings,
    ReviewMode,
    SizeTier,
    SizingResult,
    TaskType,
)
from agent_estimate.core.pert import check_metr_threshold


def _claude_frontier_config() -> EstimationConfig:
    return EstimationConfig(
        agents=[
            AgentProfile(
                name="Claude",
                capabilities=["planning", "implementation"],
                parallelism=1,
                cost_per_turn=0.12,
                model_tier="frontier",
            )
        ],
        settings=ProjectSettings(
            friction_multiplier=1.0,
            inter_wave_overhead=0.0,
            metr_fallback_threshold=45.0,
        ),
    )


def _claude_frontier_config_with_friction(friction: float) -> EstimationConfig:
    config = _claude_frontier_config()
    return EstimationConfig(
        agents=config.agents,
        settings=ProjectSettings(
            friction_multiplier=friction,
            inter_wave_overhead=0.0,
            metr_fallback_threshold=45.0,
        ),
    )


class TestPipelineMetrMapping:
    def test_loaded_policy_warning_names_unmeasured_basis(self) -> None:
        result = check_metr_threshold("opus_4_7", 100.0)

        assert result is not None
        assert "local reliability policy (unmeasured)" in result.message
        assert "METR" not in result.message

    def test_every_registry_row_has_provenance(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        raw = yaml.safe_load((repo_root / "metr_thresholds.yaml").read_text(encoding="utf-8"))

        for model_key, entry in raw["models"].items():
            assert entry["basis"] in {"measured", "extrapolated", "local-policy"}, model_key
            assert entry["source"], model_key
            assert entry["source_version"], model_key
            assert entry["as_of"], model_key

    def test_unknown_model_without_fallback_is_unavailable(self, caplog) -> None:
        with caplog.at_level("WARNING", logger="agent_estimate"):
            result = check_metr_threshold(
                "unknown_model",
                50.0,
                thresholds={},
                fallback_threshold=None,
            )

        assert result is None
        assert "Reliability horizon unavailable" in caplog.text

    def test_claude_assigned_task_uses_opus_threshold(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _pipeline,
            "classify_task",
            lambda _description: SizingResult(
                tier=SizeTier.XL,
                baseline_optimistic=90.0,
                baseline_most_likely=180.0,
                baseline_pessimistic=360.0,
                task_type=TaskType.FEATURE,
                signals=("test",),
            ),
        )
        report = run_estimate_pipeline(
            ["deterministic"],
            _claude_frontier_config(),
            review_mode=ReviewMode.NONE,
        )
        task = report.tasks[0]
        assert task.agent == "Claude"
        assert task.metr_warning is not None
        assert "opus" in task.metr_warning
        assert "(90m)" in task.metr_warning

    def test_no_false_positive_for_claude_task_at_or_below_90m(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _pipeline,
            "classify_task",
            lambda _description: SizingResult(
                tier=SizeTier.S,
                baseline_optimistic=12.0,
                baseline_most_likely=23.0,
                baseline_pessimistic=40.0,
                task_type=TaskType.FEATURE,
                signals=("test",),
            ),
        )
        report = run_estimate_pipeline(
            ["deterministic"],
            _claude_frontier_config(),
            review_mode=ReviewMode.STANDARD,
        )
        task = report.tasks[0]
        assert task.agent == "Claude"
        assert task.metr_warning is None

    def test_assigned_task_metr_recheck_includes_friction(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _pipeline,
            "classify_task",
            lambda _description: SizingResult(
                tier=SizeTier.M,
                baseline_optimistic=30.0,
                baseline_most_likely=50.0,
                baseline_pessimistic=85.0,
                task_type=TaskType.FEATURE,
                signals=("test",),
            ),
        )

        report = run_estimate_pipeline(
            ["deterministic"],
            _claude_frontier_config_with_friction(2.0),
            review_mode=ReviewMode.STANDARD,
        )

        task = report.tasks[0]
        assert task.metr_warning is not None
        assert (
            "Work estimate (105.0m) exceeds opus_5_5 local reliability policy "
            "(unmeasured) (90m)"
        ) in task.metr_warning

    def test_assigned_task_horizon_excludes_review(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _pipeline,
            "classify_task",
            lambda _description: SizingResult(
                tier=SizeTier.M,
                baseline_optimistic=80.0,
                baseline_most_likely=80.0,
                baseline_pessimistic=80.0,
                task_type=TaskType.FEATURE,
                signals=("test",),
            ),
        )

        report = run_estimate_pipeline(
            ["deterministic"],
            _claude_frontier_config(),
            review_mode=ReviewMode.STANDARD,
        )

        assert report.tasks[0].effective_duration_minutes == 80.0
        assert report.tasks[0].review_overhead_minutes == 15.0
        assert report.tasks[0].metr_warning is None

    def test_documentation_human_equivalent_uses_work_only(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _pipeline,
            "classify_task",
            lambda _description: SizingResult(
                tier=SizeTier.S,
                baseline_optimistic=12.0,
                baseline_most_likely=23.0,
                baseline_pessimistic=40.0,
                task_type=TaskType.DOCS,
                signals=("test",),
            ),
        )
        report = run_estimate_pipeline(
            ["fix typo in README"],
            _claude_frontier_config(),
            review_mode=ReviewMode.STANDARD,
            task_category=EstimationCategory.DOCUMENTATION,
        )

        task = report.tasks[0]
        assert task.effective_duration_minutes == pytest.approx(25.833333333333332)
        assert task.review_overhead_minutes == 15.0
        assert task.human_equivalent_minutes == pytest.approx(109.6, abs=0.05)
