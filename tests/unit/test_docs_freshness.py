"""Docs freshness guards (#28, #53).

README and examples/*.md embed captured CLI output. These tests re-run the same
estimates and assert the embedded examples still match what the tool actually
prints, so a prior, multiplier, or reliability-policy key bump fails CI instead
of silently rotting the docs.
"""

import re
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml
from typer.testing import CliRunner

from agent_estimate.cli.app import app

ROOT = Path(__file__).resolve().parents[2]

# The documented input from examples/multi-agent.md "Input file" block.
MULTI_AGENT_TASKS = [
    "Implement add-agent CLI command with SPEC.md generation",
    "Add known_debt.md as standard protocol memory file",
    "Write quickstart guide with protocol comparison table",
]

# Headline output lines embedded verbatim in README.md and
# examples/multi-agent.md. If the CLI stops producing them, both docs
# need re-capturing.
HEADLINE_LINES = [
    "| Expected case | 75.4m |",
    "| Compression ratio | 6.28x |",
    "Work estimate (60.4m) exceeds gpt_6_sol local reliability policy (unmeasured) (60m)",
    "Work estimate (60.4m) exceeds gemini_3_1_pro local reliability policy (unmeasured) (45m)",
]

runner = CliRunner()


@dataclass(frozen=True)
class ExampleCase:
    path: str
    args: tuple[str, ...]
    index_label: str
    index_type: str
    index_tier: str
    tasks: tuple[str, ...] = ()


EXAMPLE_CASES = [
    ExampleCase(
        path="examples/coding-s.md",
        args=("estimate", "Fix pyproject.toml URLs after org rename"),
        index_label="Fix pyproject URLs after org rename",
        index_type="Coding",
        index_tier="XS",
    ),
    ExampleCase(
        path="examples/coding-m.md",
        args=("estimate", "Implement add-agent CLI command with SPEC.md generation"),
        index_label="Implement CLI command with code generation",
        index_type="Coding",
        index_tier="M",
    ),
    ExampleCase(
        path="examples/research.md",
        args=(
            "estimate",
            "Audit cloud infrastructure providers for production deployment",
            "--type",
            "research",
        ),
        index_label="Audit cloud infrastructure providers",
        index_type="Research",
        index_tier="S",
    ),
    ExampleCase(
        path="examples/documentation.md",
        args=(
            "estimate",
            "Write quickstart guide and README with protocol comparison table",
            "--type",
            "documentation",
        ),
        index_label="Write quickstart guide + README",
        index_type="Documentation",
        index_tier="S",
    ),
    ExampleCase(
        path="examples/multi-agent.md",
        args=("estimate", "--file", "{task_file}"),
        index_label="3-agent parallel session (3 features)",
        index_type="Multi-agent",
        index_tier="M×3",
        tasks=tuple(MULTI_AGENT_TASKS),
    ),
]


@cache
def _invoke_example(case: ExampleCase) -> str:
    args = list(case.args)
    if case.tasks:
        with TemporaryDirectory() as tmp:
            task_file = Path(tmp) / f"{Path(case.path).stem}-tasks.txt"
            task_file.write_text("\n".join(case.tasks) + "\n", encoding="utf-8")
            args = [str(task_file) if arg == "{task_file}" else arg for arg in args]
            result = runner.invoke(app, args)
    else:
        result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def _invoke_args(args: tuple[str, ...]) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def _captured_output(output: str) -> str:
    lines = output.strip().splitlines()
    if lines and lines[0] == "# Agent Estimate Report":
        lines = lines[1:]
        if lines and not lines[0]:
            lines = lines[1:]
    return "\n".join("#" + line if line.startswith("## ") else line for line in lines)


def _compression_ratio(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("| Compression ratio |"):
            return line.split("|")[2].strip()
    raise AssertionError("output did not include a Compression ratio row")


def _timeline_value(output: str, metric: str) -> str:
    prefix = f"| {metric} |"
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split("|")[2].strip()
    raise AssertionError(f"output did not include {metric!r}")


def _single_task_values(output: str) -> tuple[str, str]:
    for line in output.splitlines():
        if line.startswith("| **"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            return cells[6], cells[7]
    raise AssertionError("output did not include a single-task estimate row")


def _key_takeaway(text: str) -> str:
    start = text.index("## Key takeaway")
    return text[start:]


def _real_output() -> str:
    return _invoke_example(EXAMPLE_CASES[-1])


class TestDocsFreshness:
    def test_example_input_block_lists_the_tasks(self):
        example = (ROOT / "examples" / "multi-agent.md").read_text()
        for task in MULTI_AGENT_TASKS:
            assert task in example, f"examples/multi-agent.md lost input task {task!r}"

    def test_readme_hero_matches_real_run(self):
        output = _real_output()
        readme = (ROOT / "README.md").read_text()
        for line in HEADLINE_LINES:
            assert line in output, (
                f"CLI no longer prints {line!r} for the documented tasks — "
                "re-capture the README hero block and examples/multi-agent.md"
            )
            assert line in readme, f"README hero block lost {line!r}"

    @pytest.mark.parametrize("case", EXAMPLE_CASES, ids=[case.path for case in EXAMPLE_CASES])
    def test_example_output_matches_real_run(self, case: ExampleCase):
        output = _captured_output(_invoke_example(case))
        example = (ROOT / case.path).read_text(encoding="utf-8")
        assert output in example, (
            f"{case.path} output block is stale — re-capture it from the current CLI"
        )

    def test_examples_index_compression_matches_real_runs(self):
        index = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
        for case in EXAMPLE_CASES:
            output = _invoke_example(case)
            compression = _compression_ratio(output)
            expected_row = (
                f"| {case.index_label} | {case.index_type} | {case.index_tier} | "
                f"{compression} | [{Path(case.path).name}](./{Path(case.path).name}) |"
            )
            assert expected_row in index

    def test_example_case_list_covers_all_shipped_examples(self):
        example_files = {
            path.name for path in (ROOT / "examples").glob("*.md") if path.name != "README.md"
        }
        covered_files = {Path(case.path).name for case in EXAMPLE_CASES}
        assert covered_files == example_files

    @pytest.mark.parametrize(
        ("path", "args"),
        [
            (
                "examples/coding-s.md",
                ("estimate", "Fix pyproject.toml URLs after org rename", "--review-mode", "none"),
            ),
            (
                "examples/documentation.md",
                (
                    "estimate",
                    "Write quickstart guide and README with protocol comparison table",
                    "--type",
                    "documentation",
                    "--review-mode",
                    "none",
                ),
            ),
        ],
    )
    def test_review_mode_none_prose_matches_real_run(
        self, path: str, args: tuple[str, ...]
    ):
        output = _invoke_args(args)
        effective_duration, human_equivalent = _single_task_values(output)
        expected_case = _timeline_value(output, "Expected case")
        compression = _timeline_value(output, "Compression ratio")
        example = _key_takeaway((ROOT / path).read_text(encoding="utf-8"))

        for value in (effective_duration, human_equivalent, expected_case, compression):
            assert value in example

    def test_session_command_is_documented_outside_changelog(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "agent-estimate session" in readme

    def test_report_states_five_minute_cost_assumption(self):
        output = _invoke_example(EXAMPLE_CASES[0])
        assert "Cost is a heuristic that assumes one agent turn per 5 minutes" in output

    def test_release_checklist_mentions_floating_v0_action_tag(self):
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert "floating public Action tag" in contributing
        assert "git tag -f v0" in contributing

    def test_calibration_provenance_manifest_names_four_dated_ledgers(self):
        manifest = yaml.safe_load(
            (ROOT / "docs" / "calibration" / "provenance.yaml").read_text(
                encoding="utf-8"
            )
        )

        assert manifest["schema_version"] == 1
        assert set(manifest["ledgers"]) == {
            "coding_dispatches",
            "brainstorm_dispatches",
            "elevated_thinking_dispatches",
            "coordinator_validation_corpus",
        }
        for ledger in manifest["ledgers"].values():
            window = ledger["observation_window"]
            assert window["start"] <= window["end"]
            assert ledger["sources"]
            assert ledger["fed"]

    def test_release_export_contains_no_private_staging_material(self):
        # The export set is defined by Git attributes, so this can only run in a
        # checkout. The sdist ships this file but carries no .git, where the bare
        # `git ls-files` below raised instead of skipping (found in PR #80).
        if not (ROOT / ".git").exists():
            pytest.skip("export boundary is only checkable from a git checkout")

        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        tracked = [path for path in tracked if path]
        attributes = subprocess.run(
            ["git", "check-attr", "-z", "--stdin", "export-ignore"],
            cwd=ROOT,
            check=True,
            input=b"\0".join(tracked) + b"\0",
            capture_output=True,
        ).stdout.split(b"\0")
        exported = {
            path.decode()
            for path, _attribute, value in zip(
                attributes[0::3], attributes[1::3], attributes[2::3]
            )
            if path and value != b"set"
        }
        private_fixture = "tests/fixtures/github_issue_mode_regressions.json"
        private_repo = re.compile(rb"github\.com/kiloloop/[A-Za-z0-9._-]+-dev")

        assert private_fixture not in exported
        offenders = [
            path for path in exported if private_repo.search((ROOT / path).read_bytes())
        ]
        assert offenders == []
