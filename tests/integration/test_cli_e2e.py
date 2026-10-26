"""End-to-end CLI integration tests using Typer's CliRunner."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_estimate.adapters.sqlite_store import SQLiteCalibrationStore
from agent_estimate.audit import reset_audit_logger
from agent_estimate.cli.app import app
from agent_estimate.cli.commands import estimate as estimate_command

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_flag_prints_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "agent-estimate" in result.output

    def test_version_flag_short_form_not_supported(self) -> None:
        # Typer only supports --version (no -V by default)
        result = runner.invoke(app, ["-V"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Estimate — single task
# ---------------------------------------------------------------------------


class TestEstimateSingleTask:
    def test_estimate_single_task_default_config(self) -> None:
        result = runner.invoke(app, ["estimate", "Add login button"])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_single_task_with_config(self) -> None:
        config = str(FIXTURES / "simple_linear.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "Add login button"])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_single_task_parallel_config(self) -> None:
        config = str(FIXTURES / "parallel_fanout.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "Build REST API"])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_single_task_minimal_config(self) -> None:
        config = str(FIXTURES / "minimal_agents.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "Fix typo"])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_custom_title(self) -> None:
        result = runner.invoke(
            app, ["estimate", "--title", "My Custom Report", "Add login button"]
        )
        assert result.exit_code == 0
        assert "My Custom Report" in result.output

    def test_estimate_compact_omits_single_wave(self) -> None:
        result = runner.invoke(app, ["estimate", "--compact", "Add login button"])

        assert result.exit_code == 0
        assert "## Wave Plan" not in result.output
        assert "## Agent Load Summary" in result.output

    def test_estimate_emits_audit_events(self, monkeypatch, tmp_path: Path) -> None:
        audit_log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_ENABLED", "1")
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_DESTINATION", str(audit_log))
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_LEVEL", "INFO")
        reset_audit_logger()

        config = str(FIXTURES / "minimal_agents.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "Add login button"])

        reset_audit_logger()
        assert result.exit_code == 0
        events = [
            json.loads(line)
            for line in audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_types = {event["event_type"] for event in events}
        assert "configuration_change" in event_types
        assert "estimation_request" in event_types

        config_event = next(event for event in events if event["event_type"] == "configuration_change")
        assert config_event["details"]["trigger"] == "cli --config"
        assert config_event["details"]["source"] == "minimal_agents.yaml"
        assert config_event["details"]["changed_fields"]

        estimation_event = next(event for event in events if event["event_type"] == "estimation_request")
        assert estimation_event["details"]["request"]["input_source"] == "task"
        assert estimation_event["details"]["result"]["expected_case_minutes"] > 0

    def test_estimate_with_custom_config_tolerates_missing_packaged_baseline(
        self,
        monkeypatch,
    ) -> None:
        config = str(FIXTURES / "minimal_agents.yaml")
        calls = {"count": 0}

        def fake_load_default_config():
            calls["count"] += 1
            raise FileNotFoundError("default_agents.yaml missing")

        monkeypatch.setattr(estimate_command, "load_default_config", fake_load_default_config)
        result = runner.invoke(app, ["estimate", "--config", config, "Add login button"])

        assert calls["count"] == 1
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output


class TestEstimateModifierFlags:
    def test_modifier_flags_affect_report_for_text_input(self) -> None:
        result = runner.invoke(
            app,
            [
                "estimate",
                "--spec-clarity",
                "0.3",
                "--warm-context",
                "0.3",
                "Add login button",
            ],
        )
        assert result.exit_code == 0
        assert "spec 0.30 x warm 0.30 x fit 1.00 = 0.10" in result.output

    def test_modifier_flags_work_with_file_input(self) -> None:
        task_file = str(FIXTURES / "tasks_multi.txt")
        result = runner.invoke(
            app,
            [
                "estimate",
                "--file",
                task_file,
                "--spec-clarity",
                "0.6",
                "--warm-context",
                "0.5",
                "--agent-fit",
                "1.1",
            ],
        )
        assert result.exit_code == 0
        assert "spec 0.60 x warm 0.50 x fit 1.10 = 0.33" in result.output

    def test_modifier_flags_work_with_issues_input(self, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        class _FakeGitHubAdapter:
            def fetch_task_descriptions_by_numbers(
                self, repo: str, issue_numbers: list[int]
            ) -> list[str]:
                assert repo == "kiloloop/agent-estimate"
                assert issue_numbers == [11, 12]
                return ["Implement auth flow", "Add tests"]

        monkeypatch.setattr(estimate_command, "GitHubGhCliAdapter", _FakeGitHubAdapter)
        result = runner.invoke(
            app,
            [
                "estimate",
                "--issues",
                "#11 #12",
                "--repo",
                "kiloloop/agent-estimate",
                "--spec-clarity",
                "0.7",
                "--warm-context",
                "0.6",
            ],
        )
        assert result.exit_code == 0
        assert result.output.count("spec 0.70 x warm 0.60 x fit 1.00 = 0.42") == 2

    def test_issues_input_uses_rest_adapter_when_token_is_set(
        self,
        monkeypatch,
    ) -> None:
        calls: dict[str, bool] = {}

        class _FakeRestAdapter:
            def fetch_task_descriptions_by_numbers(
                self, repo: str, issue_numbers: list[int]
            ) -> list[str]:
                assert repo == "kiloloop/agent-estimate"
                assert issue_numbers == [11]
                calls["rest"] = True
                return ["Add tests"]

        class _UnexpectedGhAdapter:
            def fetch_task_descriptions_by_numbers(
                self, repo: str, issue_numbers: list[int]
            ) -> list[str]:
                raise AssertionError("gh CLI adapter should not be used")

        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setattr(estimate_command, "GitHubRestAdapter", _FakeRestAdapter)
        monkeypatch.setattr(estimate_command, "GitHubGhCliAdapter", _UnexpectedGhAdapter)

        result = runner.invoke(
            app,
            [
                "estimate",
                "--issues",
                "11",
                "--repo",
                "kiloloop/agent-estimate",
            ],
        )

        assert result.exit_code == 0
        assert calls == {"rest": True}

    def test_modifier_out_of_range_with_issues_is_user_facing_error(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        class _FakeGitHubAdapter:
            def fetch_task_descriptions_by_numbers(
                self, repo: str, issue_numbers: list[int]
            ) -> list[str]:
                assert repo == "kiloloop/agent-estimate"
                assert issue_numbers == [11, 12]
                return ["Implement auth flow", "Add tests"]

        monkeypatch.setattr(estimate_command, "GitHubGhCliAdapter", _FakeGitHubAdapter)
        result = runner.invoke(
            app,
            [
                "estimate",
                "--issues",
                "11,12",
                "--repo",
                "kiloloop/agent-estimate",
                "--spec-clarity",
                "0.2",
            ],
        )
        assert result.exit_code != 0
        assert "Estimation error:" in result.output
        assert "spec_clarity must be between 0.3 and 1.3" in result.output

    def test_modifier_out_of_range_is_user_facing_error(self) -> None:
        result = runner.invoke(
            app,
            [
                "estimate",
                "--spec-clarity",
                "0.2",
                "Add login button",
            ],
        )
        assert result.exit_code != 0
        assert "Estimation error:" in result.output
        assert "spec_clarity must be between 0.3 and 1.3" in result.output

    def test_estimate_help_includes_modifier_flags(self) -> None:
        result = runner.invoke(app, ["estimate", "--help"])
        assert result.exit_code == 0
        normalized = _ANSI_ESCAPE_RE.sub("", result.output)
        compact = re.sub(r"\s+", "", normalized)
        assert "--spec-clarity" in compact
        assert "--warm-context" in compact
        assert "--agent-fit" in compact
        assert "0.3to1.3" in compact
        assert "0.3to1.15" in compact
        assert "0.9to1.2" in compact


# ---------------------------------------------------------------------------
# Estimate — file input
# ---------------------------------------------------------------------------


class TestEstimateFileInput:
    def test_estimate_file_input(self) -> None:
        task_file = str(FIXTURES / "tasks_multi.txt")
        result = runner.invoke(app, ["estimate", "--file", task_file])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_file_with_config(self) -> None:
        task_file = str(FIXTURES / "tasks_multi.txt")
        config = str(FIXTURES / "parallel_fanout.yaml")
        result = runner.invoke(app, ["estimate", "--file", task_file, "--config", config])
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_estimate_file_not_found(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nonexistent.txt")
        result = runner.invoke(app, ["estimate", "--file", missing])
        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_estimate_file_directory_is_user_facing_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["estimate", "--file", str(tmp_path)])
        assert result.exit_code == 2
        assert "Failed to read task file" in result.output

    def test_estimate_file_non_utf8_is_user_facing_error(self, tmp_path: Path) -> None:
        task_file = tmp_path / "tasks.txt"
        task_file.write_bytes(b"\xff\xfe\x00")
        result = runner.invoke(app, ["estimate", "--file", str(task_file)])
        assert result.exit_code == 2
        assert "Failed to decode task file" in result.output


# ---------------------------------------------------------------------------
# Estimate — review modes
# ---------------------------------------------------------------------------


class TestEstimateReviewModes:
    def test_review_mode_none(self) -> None:
        result = runner.invoke(app, ["estimate", "--review-mode", "none", "Add button"])
        assert result.exit_code == 0

    def test_review_mode_standard(self) -> None:
        result = runner.invoke(app, ["estimate", "--review-mode", "standard", "Add button"])
        assert result.exit_code == 0

    def test_review_mode_complex(self) -> None:
        result = runner.invoke(app, ["estimate", "--review-mode", "complex", "Add button"])
        assert result.exit_code == 0

    def test_review_mode_three_round(self) -> None:
        result = runner.invoke(app, ["estimate", "--review-mode", "3-round", "Add button"])
        assert result.exit_code == 0

    def test_review_mode_invalid(self) -> None:
        result = runner.invoke(app, ["estimate", "--review-mode", "bogus", "Add button"])
        assert result.exit_code != 0
        assert "Invalid review mode" in result.output

    def test_review_mode_legacy_self_accepted(self) -> None:
        """Legacy 'self' mode still accepted for backwards compatibility."""
        result = runner.invoke(app, ["estimate", "--review-mode", "self", "Add button"])
        assert result.exit_code == 0

    def test_review_mode_legacy_2x_lgtm_accepted(self) -> None:
        """Legacy '2x-lgtm' mode still accepted for backwards compatibility."""
        result = runner.invoke(app, ["estimate", "--review-mode", "2x-lgtm", "Add button"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Estimate — format
# ---------------------------------------------------------------------------


class TestEstimateFormat:
    def test_format_markdown(self) -> None:
        result = runner.invoke(app, ["estimate", "--format", "markdown", "Add button"])
        assert result.exit_code == 0

    def test_format_json(self) -> None:
        import json

        result = runner.invoke(app, ["estimate", "--format", "json", "Add button"])
        if result.exit_code == 0:
            # JSON renderer is available — validate parseable JSON output
            data = json.loads(result.output)
            assert isinstance(data, dict)
        else:
            # JSON renderer not yet wired — expect graceful not-implemented message
            assert "not yet implemented" in result.output

    def test_format_unknown(self) -> None:
        result = runner.invoke(app, ["estimate", "--format", "xml", "Add button"])
        assert result.exit_code != 0
        assert "Unknown format" in result.output

    def test_format_unknown_does_not_emit_estimation_audit(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        audit_log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_ENABLED", "1")
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_DESTINATION", str(audit_log))
        monkeypatch.setenv("AGENT_ESTIMATE_AUDIT_LEVEL", "INFO")
        reset_audit_logger()

        result = runner.invoke(app, ["estimate", "--format", "xml", "Add button"])

        reset_audit_logger()
        assert result.exit_code != 0
        assert not audit_log.exists()

    def test_format_unknown_with_issues_does_not_fetch_github(self, monkeypatch) -> None:
        class _FailingGitHubAdapter:
            def fetch_task_descriptions_by_numbers(
                self, repo: str, issue_numbers: list[int]
            ) -> list[str]:
                raise AssertionError("GitHub fetch should not run for invalid format")

        monkeypatch.setattr(estimate_command, "GitHubGhCliAdapter", _FailingGitHubAdapter)
        result = runner.invoke(
            app,
            [
                "estimate",
                "--issues",
                "11",
                "--repo",
                "kiloloop/agent-estimate",
                "--format",
                "xml",
            ],
        )

        assert result.exit_code != 0
        assert "Unknown format" in result.output


# ---------------------------------------------------------------------------
# Estimate — error cases
# ---------------------------------------------------------------------------


class TestEstimateErrors:
    def test_no_input_shows_error(self) -> None:
        result = runner.invoke(app, ["estimate"])
        assert result.exit_code != 0

    def test_task_and_file_mutual_exclusion(self, tmp_path: Path) -> None:
        task_file = tmp_path / "tasks.txt"
        task_file.write_text("Some task\n")
        result = runner.invoke(
            app, ["estimate", "inline task", "--file", str(task_file)]
        )
        assert result.exit_code != 0
        assert "only one input source" in result.output.lower() or "Provide only one" in result.output

    def test_config_file_not_found(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "no_such_config.yaml")
        result = runner.invoke(app, ["estimate", "--config", missing, "task"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_config_directory_is_user_facing_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["estimate", "--config", str(tmp_path), "task"])
        assert result.exit_code == 2
        assert "Failed to read config file" in result.output

    def test_config_invalid_validation(self) -> None:
        config = str(FIXTURES / "cycle_invalid.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "task"])
        assert result.exit_code != 0

    def test_config_empty_agents(self) -> None:
        config = str(FIXTURES / "malformed_missing_agent.yaml")
        result = runner.invoke(app, ["estimate", "--config", config, "task"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Calibrate
# ---------------------------------------------------------------------------


class TestCalibrate:
    def test_calibrate_no_db(self) -> None:
        result = runner.invoke(app, ["calibrate"])
        assert result.exit_code != 0
        assert "No calibration database" in result.output

    def test_calibrate_nonexistent_db(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "missing.db")
        result = runner.invoke(app, ["calibrate", "--db", db_path])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_valid_observation(self) -> None:
        obs = str(FIXTURES / "observation_valid.yaml")
        result = runner.invoke(app, ["validate", obs])
        assert result.exit_code == 0
        assert "Estimation vs Actual Comparison" in result.output
        assert "ACCURATE" in result.output

    def test_validate_file_not_found(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nope.yaml")
        result = runner.invoke(app, ["validate", missing])
        assert result.exit_code != 0
        assert "File not found" in result.output

    def test_validate_malformed_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: a: valid: yaml: [")
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code != 0

    def test_validate_missing_required_fields(self, tmp_path: Path) -> None:
        incomplete = tmp_path / "incomplete.yaml"
        incomplete.write_text("task_type: feature\n")
        result = runner.invoke(app, ["validate", str(incomplete)])
        assert result.exit_code != 0

    @pytest.mark.parametrize(
        ("body", "expected_error"),
        [
            ("estimated_minutes: .inf\nactual_work_minutes: 5", "estimated_minutes must be finite"),
            ("estimated_minutes: 10\nactual_work_minutes: .nan", "actual_work_minutes must be finite"),
            ("estimated_minutes: 10\nactual_work_minutes: .inf", "actual_work_minutes must be finite"),
            ("estimated_minutes: 10\nactual_work_minutes: -5", "actual_work_minutes must be >= 0"),
            (
                "estimated_minutes: 10\nactual_work_minutes: 40\nactual_total_minutes: 10",
                "actual_total_minutes must be >= actual_work_minutes",
            ),
            (
                "estimated_minutes: 10\nactual_work_minutes: 5\nactual_total_minutes: .inf",
                "actual_total_minutes must be finite",
            ),
        ],
    )
    def test_validate_rejects_invalid_core_numeric_fields(
        self,
        tmp_path: Path,
        body: str,
        expected_error: str,
    ) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(body, encoding="utf-8")

        result = runner.invoke(app, ["validate", str(observation)])

        assert result.exit_code == 2
        assert expected_error in result.output
        assert "Verdict:" not in result.output

    def test_validate_bad_optional_field_is_input_error(self, tmp_path: Path) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(
            "estimated_minutes: 10\nactual_work_minutes: 12\nreview_overhead_minutes: abc",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(observation), "--db", str(tmp_path / "c.db")])
        assert result.exit_code == 2
        assert "Invalid observation field" in result.output
        assert "Error storing observation" not in result.output

    def test_validate_with_db_stores_observation_round_trip(self, tmp_path: Path) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(
            "task_type: feature\nestimated_minutes: 10\nactual_work_minutes: 12\nactual_total_minutes: 15\nfile_count: 2\nline_count: 80\ntest_count: 3\nproject_hash: proj-abc\nexecution_mode: batch\nreview_mode: standard\nreview_overhead_minutes: 3\nmodifiers:\n  spec_clarity: 0.8\n  warm_context: 0.7\nmodifiers_should_have_been:\n  spec_clarity: 0.9",
            encoding="utf-8",
        )
        db_path = tmp_path / "calibration.db"

        before = datetime.now(timezone.utc)
        result = runner.invoke(app, ["validate", str(observation), "--db", str(db_path)])
        after = datetime.now(timezone.utc)

        assert result.exit_code == 0
        assert "Observation stored" in result.output

        with SQLiteCalibrationStore(db_path) as store:
            rows = store._query_observations(task_type="feature")

        assert len(rows) == 1
        row = rows[0]
        assert row["estimated_secs"] == 600.0
        assert row["actual_work_secs"] == 720.0
        assert row["actual_total_secs"] == 900.0
        assert row["file_count"] == 2
        assert row["line_count"] == 80
        assert row["test_count"] == 3
        assert row["project_hash"] == "proj-abc"
        assert row["execution_mode"] == "batch"
        assert row["review_mode"] == "standard"
        assert row["spec_clarity_modifier"] == 0.8
        assert row["warm_context_modifier"] == 0.7
        assert row["review_overhead_secs"] == 180.0
        assert json.loads(row["modifiers_should_have_been"]) == {"spec_clarity": 0.9}

        observed_at = datetime.fromisoformat(row["observed_at"])
        assert before.replace(microsecond=0) <= observed_at <= after.replace(microsecond=0)

    def test_validate_with_db_stores_default_observation_fields(
        self, tmp_path: Path
    ) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(
            "task_type: bugfix\nestimated_minutes: 10\nactual_work_minutes: 12",
            encoding="utf-8",
        )
        db_path = tmp_path / "calibration.db"

        result = runner.invoke(app, ["validate", str(observation), "--db", str(db_path)])

        assert result.exit_code == 0
        with SQLiteCalibrationStore(db_path) as store:
            rows = store._query_observations(task_type="bugfix")

        assert len(rows) == 1
        row = rows[0]
        assert row["file_count"] == 0
        assert row["line_count"] == 0
        assert row["test_count"] == 0
        assert row["project_hash"] == "unknown"
        assert row["execution_mode"] == "single"
        assert row["review_mode"] == "none"
        assert row["spec_clarity_modifier"] == 1.0
        assert row["warm_context_modifier"] == 1.0
        assert row["review_overhead_secs"] == 0.0
        assert json.loads(row["modifiers_should_have_been"]) == {}

    def test_validate_db_null_field_is_input_error(self, tmp_path: Path) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(
            "estimated_minutes: 10\nactual_work_minutes: 12\nfile_count:",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["validate", str(observation), "--db", str(tmp_path / "c.db")])

        assert result.exit_code == 2
        assert "Invalid observation field" in result.output
        assert "Error storing observation" not in result.output
        assert "Traceback" not in result.output

    @pytest.mark.parametrize(
        "field_body",
        [
            "file_count:\n  - 1",
            "file_count:\n  value: 1",
        ],
    )
    def test_validate_db_container_field_is_input_error(
        self, tmp_path: Path, field_body: str
    ) -> None:
        observation = tmp_path / "obs.yaml"
        observation.write_text(
            f"estimated_minutes: 10\nactual_work_minutes: 12\n{field_body}",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["validate", str(observation), "--db", str(tmp_path / "c.db")])

        assert result.exit_code == 2
        assert "Invalid observation field" in result.output
        assert "Error storing observation" not in result.output
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# No args / help
# ---------------------------------------------------------------------------


class TestNoArgs:
    def test_no_args_shows_usage(self) -> None:
        result = runner.invoke(app, [])
        # no_args_is_help=True causes Click/Typer to exit with code 2
        assert "usage" in result.output.lower() or "estimate" in result.output.lower()

    def test_help_flag(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "estimate" in result.output.lower()

    def test_estimate_help(self) -> None:
        result = runner.invoke(app, ["estimate", "--help"])
        assert result.exit_code == 0
        assert "task" in result.output.lower()


# ---------------------------------------------------------------------------
# Estimate — history file
# ---------------------------------------------------------------------------


class TestEstimateHistoryFile:
    def test_history_file_produces_report(self) -> None:
        history = str(FIXTURES / "dispatch_history.json")
        result = runner.invoke(
            app, ["estimate", "--history-file", history, "Add a button"]
        )
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_nonexistent_history_file_graceful_fallback(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "no_such_history.json")
        result = runner.invoke(
            app, ["estimate", "--history-file", missing, "Add a button"]
        )
        assert result.exit_code == 0
        assert "Agent Estimate Report" in result.output

    def test_history_file_warm_context_in_json_output(self, tmp_path: Path) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        history = tmp_path / "history.json"
        history.write_text(
            json.dumps({"dispatches": [
                {"agent": "codex", "project": "proj", "completed_at": recent}
            ]})
        )
        result = runner.invoke(
            app, ["estimate", "--history-file", str(history), "--format", "json", "Add a button"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        task = data["tasks"][0]
        assert task["modifiers"]["warm_context"] < 1.0

    def test_history_agent_filter_scopes_inference(self, tmp_path: Path) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        history = tmp_path / "history.json"
        history.write_text(
            json.dumps({"dispatches": [
                {"agent": "claude", "project": "proj", "completed_at": recent},
                {"agent": "codex", "project": "proj", "completed_at": stale},
            ]})
        )
        result = runner.invoke(
            app,
            [
                "estimate",
                "--history-file", str(history),
                "--history-agent", "codex",
                "--format", "json",
                "Add a button",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        task = data["tasks"][0]
        assert task["modifiers"]["warm_context"] == 1.0

    def test_explicit_warm_context_10_overrides_default_data_json(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        monkeypatch.chdir(tmp_path)
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        Path("data.json").write_text(
            json.dumps(
                {
                    "dispatches": [
                        {"agent": "codex", "project": "proj", "completed_at": recent}
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            ["estimate", "--warm-context", "1.0", "--format", "json", "Add a button"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        task = data["tasks"][0]
        assert task["modifiers"]["warm_context"] == 1.0

    def test_github_actions_ignores_default_data_json(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from datetime import timedelta

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        Path("data.json").write_text(
            json.dumps(
                {
                    "dispatches": [
                        {"agent": "codex", "project": "proj", "completed_at": recent}
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app, ["estimate", "--format", "json", "Add a button"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tasks"][0]["modifiers"]["warm_context"] == 1.0

    def test_github_actions_honors_explicit_history_file(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from datetime import timedelta

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        history = tmp_path / "history.json"
        history.write_text(
            json.dumps(
                {
                    "dispatches": [
                        {"agent": "codex", "project": "proj", "completed_at": recent}
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "estimate",
                "--history-file",
                str(history),
                "--format",
                "json",
                "Add a button",
            ],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tasks"][0]["modifiers"]["warm_context"] < 1.0
