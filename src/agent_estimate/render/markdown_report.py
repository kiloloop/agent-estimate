"""Markdown report renderer."""

from __future__ import annotations

from agent_estimate.render.report_models import EstimationReport


def render_markdown_report(report: EstimationReport, *, compact: bool = False) -> str:
    """Render an estimation report as GitHub-compatible Markdown."""
    lines: list[str] = [
        f"# {_normalize_inline(report.title)}",
        "",
    ]
    if report.schema_version is not None:
        lines.extend([f"Schema: `{_normalize_inline(report.schema_version)}`", ""])
    if report.basis is not None:
        lines.extend([
            (f"Forecast basis: `{_normalize_inline(report.basis)}`; "
            f"source: {_normalize_inline(report.source or 'unknown')}; "
            f"as_of: {_normalize_inline(report.as_of or 'unknown')}. "
            "Admission caps are not forecast or scoring inputs."),
            "",
        ])
    lines.extend(_render_task_table(report))
    if report.tokens is not None:
        tokens = report.tokens
        total = "unavailable" if tokens.expected_tokens_total is None else f"{tokens.expected_tokens_total:,}"
        output = "unavailable" if tokens.expected_tokens_output is None else f"{tokens.expected_tokens_output:,}"
        lines.extend([
            "", "## Token Forecast", "",
            f"- Expected total processed tokens (including cache carry): {total}",
            f"- Expected output tokens: {output}",
        ])
        # The cache-read slot appears once a measurement or prior supplies it;
        # prior-only reports keep their v0.8 lines.
        if tokens.expected_tokens_cache_read is not None or tokens.basis == "measured":
            cache_read = (
                "unavailable" if tokens.expected_tokens_cache_read is None
                else f"{tokens.expected_tokens_cache_read:,}"
            )
            lines.append(f"- Expected cache-read tokens: {cache_read}")
        lines.append(
            f"- basis: `{tokens.basis}`; source: {_normalize_inline(tokens.source or 'unknown')}; "
            f"as_of: {tokens.as_of.isoformat() if tokens.as_of else 'unknown'}; "
            f"population: {_normalize_inline(tokens.population or 'unknown')}"
        )
        if tokens.segment is not None and tokens.window is not None:
            lines.append(
                f"- segment: {_normalize_inline(tokens.segment.task_type)} × "
                f"{_normalize_inline(tokens.segment.execution_profile_id)}; n: {tokens.segment.n}; "
                f"window: {tokens.window.start.isoformat()}..{tokens.window.end.isoformat()}"
            )
        lines.extend(f"- {_normalize_inline(warning)}" for warning in tokens.warnings)
    if report.subscription is not None:
        lines.extend(_render_subscription(report))
    if not compact:
        lines.extend([""])
        lines.extend(_render_wave_table(report))
    lines.extend([""])
    lines.extend(_render_timeline_summary(report))
    lines.extend([""])
    lines.extend(_render_review_overhead(report))
    lines.extend([""])
    lines.extend(_render_agent_load_table(report, include_idle=not compact))
    lines.extend([""])
    lines.extend(_render_critical_path(report))
    lines.extend([""])
    lines.extend(_render_assumptions(report))
    lines.extend([""])
    lines.extend(_render_tier_correction_warnings(report))
    lines.extend([""])
    lines.extend(_render_reliability_warnings(report))
    lines.append("")
    return "\n".join(lines)


def _render_subscription(report: EstimationReport) -> list[str]:
    subscription = report.subscription
    lines = [
        "", "## Subscription Points Forecast (experimental)", "",
        (f"- Agent: {_normalize_inline(subscription.agent_name)}; "
         f"model: {_normalize_inline(subscription.model_id or 'unknown')}"),
    ]
    meter = subscription.meter
    if meter is None:
        lines.append(
            f"- Expected points: unavailable ({_normalize_inline(subscription.unavailable_reason)})"
        )
    else:
        lines.append(
            f"- Expected points: {subscription.expected_points:.4g} of a "
            f"{meter.window_points:g}-point, {meter.window_days:g}-day window "
            f"({subscription.window_fraction * 100:.4g}% of the window)"
        )
        lines.append(
            f"- meter: {meter.points_per_noncache_million:g} points per million non-cache tokens; "
            f"{meter.points_per_cache_read_million:g} points per million cache-read tokens"
        )
    lines.append(
        f"- basis: `{subscription.basis}`; source: {_normalize_inline(subscription.source or 'unknown')}; "
        f"as_of: {subscription.as_of.isoformat() if subscription.as_of else 'unknown'}; "
        f"token basis: `{subscription.token_basis}`"
    )
    lines.extend(f"- {_normalize_inline(warning)}" for warning in subscription.warnings)
    return lines


def _render_task_table(report: EstimationReport) -> list[str]:
    critical_tasks = set(report.critical_path)
    lines = [
        "## Per-Task Estimates",
        "",
        "| Task | Model | Tier | Agent | Base PERT (O/M/P) | Modifiers | Effective Duration | Human Equivalent |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    show_profile = any(task.estimate_factor != 1.0 for task in report.tasks)
    if show_profile:
        lines[2] += " Profile modifier (work minutes) |"
        lines[3] += " --- |"
    for task in report.tasks:
        task_name = _escape_cell(task.name)
        if task.name in critical_tasks:
            task_name = f"**{task_name}**"
        model_label = task.estimation_category.value if task.estimation_category else "coding"
        base_pert = (
            f"{_format_minutes(task.base_pert_optimistic_minutes)} / "
            f"{_format_minutes(task.base_pert_most_likely_minutes)} / "
            f"{_format_minutes(task.base_pert_pessimistic_minutes)} "
            f"(E={_format_minutes(task.base_pert_expected_minutes)})"
        )
        warm_str = f"warm {task.modifier_warm_context:.2f}"
        if task.warm_context_detail:
            warm_str += f" (auto: {_escape_cell(task.warm_context_detail)})"
        modifiers = (
            f"spec {task.modifier_spec_clarity:.2f} x "
            f"{warm_str} x "
            f"fit {task.modifier_agent_fit:.2f} = {task.modifier_combined:.2f}"
        )
        human = (
            _format_minutes(task.human_equivalent_minutes)
            if task.human_equivalent_minutes is not None
            else "N/A"
        )
        profile_cell = (
            f" {task.estimate_factor:g}x: "
            f"{_format_minutes(task.work_before_adjustment_minutes)} to "
            f"{_format_minutes(task.effective_duration_minutes)} |"
            if show_profile else ""
        )
        lines.append(
            f"| {task_name} | {model_label} | {task.tier} | {_escape_cell(task.agent)} | {base_pert} | "
            f"{modifiers} | {_format_minutes(task.effective_duration_minutes)} | {human} |{profile_cell}"
        )
    return lines


def _render_wave_table(report: EstimationReport) -> list[str]:
    lines = [
        "## Wave Plan",
        "",
        "| Wave | Tasks | Duration | Agent Assignments (amortized review) |",
        "| --- | --- | --- | --- |",
    ]
    for wave in report.waves:
        tasks = ", ".join(_escape_cell(task_name) for task_name in wave.tasks) or "N/A"
        assignments: list[str] = []
        for agent in sorted(wave.agent_assignments):
            assigned_tasks = ", ".join(_escape_cell(task) for task in wave.agent_assignments[agent])
            review_m = wave.agent_review_minutes.get(agent, 0.0)
            review_note = f" +{_format_minutes(review_m)} review" if review_m > 0 else ""
            assignments.append(f"{_escape_cell(agent)}: {assigned_tasks or 'none'}{review_note}")
        assignment_text = "; ".join(assignments) if assignments else "N/A"
        lines.append(
            f"| {wave.number} | {tasks} | {_format_minutes(wave.duration_minutes)} | "
            f"{assignment_text} |"
        )
    return lines


def _render_timeline_summary(report: EstimationReport) -> list[str]:
    timeline = report.timeline
    return [
        "## Timeline Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Best case | {_format_minutes(timeline.best_case_minutes)} |",
        f"| Expected case | {_format_minutes(timeline.expected_case_minutes)} |",
        f"| Worst case | {_format_minutes(timeline.worst_case_minutes)} |",
        f"| Human-speed equivalent | {_format_minutes(timeline.human_equivalent_minutes)} |",
        f"| Compression ratio | {timeline.compression_ratio:.2f}x |",
        f"| Review overhead (per-task, pre-amortization) | {_format_minutes(report.review_overhead_minutes)} |",
    ]


def _render_review_overhead(report: EstimationReport) -> list[str]:
    lines = [
        "## Review Overhead",
        "",
        "Review is amortized per agent per wave: one review cycle covers all PRs from that",
        "agent in the wave.  Per-task values below are the naive (pre-amortization) figures.",
        "",
        "| Task | Review Overhead |",
        "| --- | --- |",
    ]
    for task in report.tasks:
        lines.append(f"| {_escape_cell(task.name)} | {_format_minutes(task.review_overhead_minutes)} |")
    lines.append(f"| **Total (naive)** | **{_format_minutes(report.review_overhead_minutes)}** |")
    return lines


def _render_agent_load_table(
    report: EstimationReport, *, include_idle: bool = True
) -> list[str]:
    lines = [
        "## Agent Load Summary",
        "",
        "| Agent | Task Count | Total Work | Estimated Cost |",
        "| --- | --- | --- | --- |",
    ]
    for load in report.agent_load:
        if not include_idle and load.task_count == 0:
            continue
        lines.append(
            f"| {_escape_cell(load.agent)} | {load.task_count} | "
            f"{_format_minutes(load.total_work_minutes)} | ${load.estimated_cost:.2f} |"
        )
    return lines


def _render_critical_path(report: EstimationReport) -> list[str]:
    lines = ["## Critical Path", ""]
    if not report.critical_path:
        lines.append("No critical path provided.")
        return lines
    path = " -> ".join(f"**{_escape_cell(task_name)}**" for task_name in report.critical_path)
    lines.append(path)
    return lines


def _render_assumptions(_report: EstimationReport) -> list[str]:
    return [
        "## Assumptions",
        "",
        "- CLI task descriptions carry no dependency edges; scheduling assumes independence.",
        "- Calibration store: n=0 observations applied; the estimate pipeline does not consume calibration feedback.",
        "- Bundled-prior thinking-level baseline: Claude Code high and Codex extra-high.",
        "- Human equivalent covers agent work only; human review is reported separately.",
        "- Cost is a heuristic that assumes one agent turn per 5 minutes of work.",
    ]


def _render_tier_correction_warnings(report: EstimationReport) -> list[str]:
    lines = ["## Tier Corrections", ""]
    warnings = [
        (task.name, task.tier_correction_warnings)
        for task in report.tasks
        if task.tier_correction_warnings
    ]
    if not warnings:
        lines.append("No tier corrections.")
        return lines
    for task_name, task_warnings in warnings:
        for warning in task_warnings:
            lines.append(f"- **{_escape_cell(task_name)}**: {_escape_cell(warning)}")
    return lines


def _render_reliability_warnings(report: EstimationReport) -> list[str]:
    lines = ["## Reliability Horizon Warnings", ""]
    warnings = [(task.name, task.metr_warning) for task in report.tasks if task.metr_warning]
    if not warnings:
        lines.append("No reliability horizon warnings.")
        return lines
    for task_name, warning in warnings:
        lines.append(f"- **{_escape_cell(task_name)}**: {_escape_cell(str(warning))}")
    return lines


def _format_minutes(value: float) -> str:
    rounded = round(value, 1)
    if abs(rounded - int(rounded)) < 1e-9:
        return f"{int(rounded)}m"
    return f"{rounded:.1f}m"


def _escape_cell(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return normalized.replace("|", "\\|")


def _normalize_inline(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
