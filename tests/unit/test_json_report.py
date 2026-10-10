"""Tests for JSON report rendering and CLI JSON output."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_estimate import __version__
from agent_estimate.cli.app import app
from agent_estimate.render.json_report import render_json_report
from agent_estimate.render.report_models import (
    EstimationReport,
    ReportAgentLoad,
    ReportTask,
    ReportTimeline,
    ReportWave,
)

_RUNNER = CliRunner()


def _build_report() -> EstimationReport:
    return EstimationReport(
        title="W3 Estimate",
        tasks=(
            ReportTask(
                name="Implement auth",
                tier="M",
                agent="Codex",
                base_pert_optimistic_minutes=25.0,
                base_pert_most_likely_minutes=50.0,
                base_pert_pessimistic_minutes=90.0,
                modifier_spec_clarity=1.1,
                modifier_warm_context=1.0,
                modifier_agent_fit=1.0,
                modifier_combined=1.1,
                modifier_raw_combined=1.1,
                modifier_clamped=False,
                effective_duration_minutes=57.8,
                human_equivalent_minutes=160.0,
                review_overhead_minutes=17.5,
                metr_warning="Estimate exceeds threshold",
                warm_context_detail="auto warm match",
                tier_correction_warnings=("Upgraded M→L: 4 concerns",),
            ),
            ReportTask(
                name="Add tests",
                tier="S",
                agent="Claude",
                base_pert_optimistic_minutes=12.0,
                base_pert_most_likely_minutes=23.0,
                base_pert_pessimistic_minutes=40.0,
                modifier_spec_clarity=1.0,
                modifier_warm_context=1.0,
                modifier_agent_fit=1.0,
                modifier_combined=1.0,
                modifier_raw_combined=1.0,
                modifier_clamped=False,
                effective_duration_minutes=24.0,
                human_equivalent_minutes=75.0,
                review_overhead_minutes=7.5,
                metr_warning=None,
            ),
        ),
        waves=(
            ReportWave(
                number=1,
                tasks=("Implement auth", "Add tests"),
                duration_minutes=85.0,
                agent_assignments={
                    "Codex": ("Implement auth",),
                    "Claude": ("Add tests",),
                },
            ),
        ),
        timeline=ReportTimeline(
            best_case_minutes=70.0,
            expected_case_minutes=90.0,
            worst_case_minutes=130.0,
            human_equivalent_minutes=250.0,
        ),
        agent_load=(
            ReportAgentLoad(
                agent="Codex",
                task_count=1,
                total_work_minutes=72.5,
                estimated_cost=18.4,
            ),
            ReportAgentLoad(
                agent="Claude",
                task_count=1,
                total_work_minutes=30.5,
                estimated_cost=9.3,
            ),
        ),
        critical_path=("Implement auth", "Add tests"),
        engine_version="test-engine",
        registry_version="test-registry",
    )


def test_render_json_report_matches_golden_fixture() -> None:
    report = _build_report()

    rendered = render_json_report(report)
    golden_path = Path(__file__).resolve().parents[1] / "fixtures" / "json_report_golden.json"
    golden = golden_path.read_text(encoding="utf-8")

    assert rendered == golden


def test_render_json_report_is_canonical_and_round_trips() -> None:
    rendered = render_json_report(_build_report())

    payload = json.loads(rendered)
    normalized = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    assert rendered == normalized
    assert json.loads(normalized) == payload
    assert {"tasks", "waves", "timeline", "agent_load", "critical_path", "metr_warnings"} <= set(
        payload
    )
    assert "warm_context_detail" in payload["tasks"][0]["modifiers"]
    assert payload["tasks"][0]["tier_correction_warnings"] == ["Upgraded M→L: 4 concerns"]
    assert payload["tier_correction_warnings"] == [
        {"task": "Implement auth", "warning": "Upgraded M→L: 4 concerns"}
    ]
    assert payload["footer"] == {
        "engine_version": "test-engine",
        "registry_version": "test-registry",
    }
    assert payload["agent_load"][0]["heuristic_cost"] == 9.3
    assert all("estimated_cost" not in row for row in payload["agent_load"])


def test_estimate_command_json_format_outputs_json() -> None:
    result = _RUNNER.invoke(
        app,
        ["estimate", "Implement OAuth login flow", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "tasks" in payload
    assert "waves" in payload
    assert "timeline" in payload
    assert payload["footer"] == {
        "engine_version": __version__,
        "registry_version": "v0.9-policy-1",
    }
