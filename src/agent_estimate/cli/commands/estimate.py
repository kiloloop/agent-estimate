"""Estimate command — full pipeline."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import typer

from agent_estimate.adapters.config_loader import load_config, load_default_config
from agent_estimate.adapters.github_adapter import GitHubAdapterError
from agent_estimate.adapters.github_ghcli import GitHubGhCliAdapter
from agent_estimate.adapters.github_rest import GitHubRestAdapter
from agent_estimate.adapters.spec_loader import (
    load_estimate_request,
    load_meter_table,
    load_token_observations,
)
from agent_estimate.audit import emit_audit_event
from agent_estimate.cli.commands._pipeline import run_estimate_pipeline
from agent_estimate.cli.commands._utils import validate_output_format
from agent_estimate.cli.commands.github import parse_issue_selection
from agent_estimate.contract import (
    EstimateRequest,
    MeterTable,
    TokenObservations,
    measured_token_forecast,
    subscription_forecast,
)
from agent_estimate.core import EstimationCategory, EstimationConfig, ReviewMode
from agent_estimate.core.history import infer_warm_context
from agent_estimate.render import render_json_report, render_markdown_report

logger = logging.getLogger("agent_estimate")


def run(
    ctx: typer.Context,
    task: str | None = typer.Argument(None, help="Task description to estimate."),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Path to a task file (one task per line)."
    ),
    spec: Path | None = typer.Option(
        None, "--spec", help="Path to a versioned EstimateRequest YAML file."
    ),
    token_observations: Path | None = typer.Option(
        None,
        "--token-observations",
        help=(
            "Observed tokens per closed leg (agent-estimate/token-observations/v1 YAML); "
            "corrects the token forecast for the spec's segment at n >= 5. Requires --spec."
        ),
    ),
    meter_table: Path | None = typer.Option(
        None,
        "--meter-table",
        help=(
            "Experimental: caller-supplied subscription meters per model id "
            "(agent-estimate/meter-table/v1 YAML); reports subscription points for the "
            "spec's assigned agent from its token forecast. Requires --spec."
        ),
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to config YAML."
    ),
    format: str = typer.Option(
        "markdown", "--format", help="Output format: markdown or json."
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        help="Use compact Markdown for a single-task comment.",
    ),
    review_mode: str = typer.Option(
        "standard",
        "--review-mode",
        help=(
            "Review overhead tier: none (0 m), standard (15 m), complex (25 m), "
            "3-round (35 m)."
        ),
    ),
    issues: str | None = typer.Option(
        None,
        "--issues",
        "-i",
        help="GitHub issue numbers (comma/space separated, '#' optional).",
    ),
    repo: str | None = typer.Option(
        None, "--repo", "-r", help="GitHub repo (owner/name)."
    ),
    title: str = typer.Option(
        "Agent Estimate Report", "--title", "-t", help="Report title."
    ),
    spec_clarity: float = typer.Option(
        1.0,
        "--spec-clarity",
        help="Spec clarity modifier (range: 0.3 to 1.3; lower means clearer spec).",
    ),
    warm_context: float | None = typer.Option(
        None,
        "--warm-context",
        help=(
            "Warm context modifier (range: 0.3 to 1.15; lower means warmer context). "
            "When omitted, auto-infers from --history-file or, outside GitHub Actions, "
            "./data.json when present."
        ),
    ),
    agent_fit: float = typer.Option(
        1.0,
        "--agent-fit",
        help="Agent fit modifier (range: 0.9 to 1.2; lower means better fit).",
    ),
    history_file: Path | None = typer.Option(
        None,
        "--history-file",
        help="Dispatch history JSON for auto warm-context detection.",
    ),
    history_agent: str | None = typer.Option(
        None,
        "--history-agent",
        help="Filter dispatch history by agent name.",
    ),
    history_project: str | None = typer.Option(
        None,
        "--history-project",
        help="Filter dispatch history by project name.",
    ),
    no_auto_tier: bool = typer.Option(
        False,
        "--no-auto-tier/--auto-tier",
        help="Disable tier auto-correction based on scope signals.",
    ),
    estimated_tests: int | None = typer.Option(
        None,
        "--estimated-tests",
        help="Expected number of tests (used for tier auto-correction).",
    ),
    estimated_lines: int | None = typer.Option(
        None,
        "--estimated-lines",
        help="Expected lines of code changed (used for tier auto-correction).",
    ),
    num_concerns: int | None = typer.Option(
        None,
        "--num-concerns",
        help="Number of distinct modules/APIs/schemas involved (used for tier auto-correction).",
    ),
    task_type: str | None = typer.Option(
        None,
        "--type",
        help=(
            "Task category: coding (default), brainstorm, research, config, "
            "documentation, frontend, app_dev. Auto-detected from description "
            "when not provided."
        ),
    ),
) -> None:
    """Estimate effort for one or more task descriptions."""
    started_at = time.perf_counter()
    config_path = config
    input_source = (
        "spec" if spec is not None else
        "task" if task is not None else "file" if file is not None else "issues"
    )

    # --- Resolve input source (exactly one) ---
    sources = sum([task is not None, file is not None, issues is not None, spec is not None])
    if sources == 0:
        _error("Provide a task description, --file, --issues, or --spec.", 2)
    if sources > 1:
        _error("Provide only one input source: task argument, --file, --issues, or --spec.", 2)
    validate_output_format(format)
    if token_observations is not None and spec is None:
        _error("--token-observations requires --spec.", 2)
    if meter_table is not None and spec is None:
        _error("--meter-table requires --spec.", 2)

    request: EstimateRequest | None = None
    observations: TokenObservations | None = None
    meters: MeterTable | None = None
    if spec is not None:
        # The file owns task/profile facts. Explicit defaults are overrides too.
        for option in (
            "review_mode", "spec_clarity", "warm_context", "agent_fit",
            "history_file", "history_agent", "history_project", "no_auto_tier",
            "estimated_tests", "estimated_lines", "num_concerns", "task_type", "repo",
        ):
            source = ctx.get_parameter_source(option)
            # Typer can provide its own enum without an installed click package.
            if source is not None and source.name == "COMMANDLINE":
                parameter = next(p for p in ctx.command.params if p.name == option)
                _error(f"--spec cannot be combined with {parameter.opts[0]}", 2)
        try:
            request = load_estimate_request(spec)
        except ValueError as exc:
            _error(f"Spec validation error: {exc}", 2)
        if token_observations is not None:
            try:
                observations = load_token_observations(token_observations)
            except ValueError as exc:
                _error(f"Token observations validation error: {exc}", 2)
        if meter_table is not None:
            try:
                meters = load_meter_table(meter_table)
            except ValueError as exc:
                _error(f"Meter table validation error: {exc}", 2)

    descriptions: list[str] = []

    if request is not None:
        # A validated contract may describe work this CLI cannot yet execute.
        # Reject operational inputs we cannot honor rather than silently estimate
        # an independent, unconstrained task with a different multiplier.
        unsupported = {
            "task_spec.dependency_task_ids": bool(request.task_spec.dependency_task_ids),
            "execution_profile.estimate_multiplier": request.execution_profile.estimate_multiplier != 1,
        }
        for field, present in unsupported.items():
            if present:
                _error(f"{field}: not supported by the current spec estimate path.", 2)
        descriptions = [f"{request.task_spec.title}\n\n{request.task_spec.description}"]
        profile = request.execution_profile
        review_mode = _spec_review_mode(request).value
        spec_clarity = profile.modifiers.spec_clarity
        warm_context = profile.modifiers.warm_context
        # ae-4 owns the context.state/warmth rule; only explicit warmth/co-dispatch applies here.
        if warm_context is None:
            warm_context = 0.5 if profile.context.implicit_co_dispatch else 1.0
        agent_fit = profile.modifiers.agent_fit
        estimated_tests = request.task_spec.scope.estimated_tests
        estimated_lines = request.task_spec.scope.estimated_lines_changed
        num_concerns = request.task_spec.scope.concerns
        task_type = request.task_spec.task_type
    elif task is not None:
        descriptions = [task]
    elif file is not None:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
            descriptions = [ln.strip() for ln in lines if ln.strip()]
        except FileNotFoundError:
            _error(f"File not found: {file}", 2)
        except UnicodeDecodeError as exc:
            _error(f"Failed to decode task file {file}: {exc}", 2)
        except OSError as exc:
            _error(f"Failed to read task file {file}: {exc}", 2)
        if not descriptions:
            _error(f"No task descriptions found in {file}.", 2)
    elif issues is not None:
        if not repo:
            _error("--repo is required when using --issues.", 2)
        try:
            issue_numbers = parse_issue_selection(issues)
        except ValueError:
            _error(f"Invalid issue numbers: {issues}", 2)
        if not issue_numbers:
            _error("No issue numbers provided.", 2)
        try:
            descriptions = _fetch_github_task_descriptions(repo, issue_numbers)
        except GitHubAdapterError as exc:
            _error(f"GitHub error: {exc}", 1)

    # --- Resolve review mode ---
    try:
        mode = ReviewMode(review_mode)
    except ValueError:
        _error(
            f"Invalid review mode: {review_mode!r}. "
            "Use none, standard, complex, or 3-round.",
            2,
        )

    # --- Load config ---
    try:
        cfg = load_config(config_path) if config_path else load_default_config()
    except FileNotFoundError:
        _error(f"Config file not found: {config_path}", 2)
    except OSError as exc:
        _error(f"Failed to read config file {config_path}: {exc}", 2)
    except ValueError as exc:
        _error(f"Config validation error: {exc}", 2)

    baseline_cfg = cfg
    if config_path:
        try:
            baseline_cfg = load_default_config()
        except (FileNotFoundError, ValueError):
            baseline_cfg = cfg
    emit_audit_event(
        "configuration_change",
        action="config_load",
        trigger="cli --config" if config_path else "packaged-default",
        source=config_path.name if config_path else "default_agents.yaml",
        changed_fields=_summarize_config_changes(cfg, baseline_cfg),
        agent_names=[agent.name for agent in cfg.agents],
    )
    if request is not None:
        cfg = _spec_config(request, cfg)

    # --- Infer warm context from dispatch history ---
    history_path = history_file
    if request is None and history_path is None and "GITHUB_ACTIONS" not in os.environ:
        default_history = Path("data.json")
        if default_history.exists():
            history_path = default_history

    warm_ctx = infer_warm_context(
        history_path, agent=history_agent, project=history_project
    )
    # Auto-inferred warm_context applies only when --warm-context was omitted.
    effective_warm_context = 1.0 if warm_context is None else warm_context
    effective_detail: str | None = None
    if warm_ctx.value != 1.0 and warm_context is None:
        effective_warm_context = warm_ctx.value
        effective_detail = warm_ctx.detail
        logger.info(
            "warm_context: %.2f (auto: %s)", warm_ctx.value, warm_ctx.detail
        )

    # --- Resolve task category ---
    estimation_category: EstimationCategory | None = None
    if task_type is not None:
        try:
            estimation_category = EstimationCategory(task_type.lower())
        except ValueError:
            valid = ", ".join(c.value for c in EstimationCategory)
            _error(f"Invalid task type: {task_type!r}. Use one of: {valid}.", 2)

    # --- Run pipeline ---
    try:
        report = run_estimate_pipeline(
            descriptions,
            cfg,
            review_mode=mode,
            title=title,
            spec_clarity=spec_clarity,
            warm_context=effective_warm_context,
            agent_fit=agent_fit,
            warm_context_detail=effective_detail,
            auto_tier=not no_auto_tier,
            estimated_tests=estimated_tests,
            estimated_lines=estimated_lines,
            num_concerns=num_concerns,
            task_category=estimation_category,
            required_capabilities=(request.task_spec.required_capabilities if request else ()),
        )
    except ValueError as exc:
        _error(f"Estimation error: {exc}", 2)
    except RuntimeError as exc:
        _error(f"Runtime error: {exc}", 1)

    if request is not None:
        # Observations always yield a labeled token block, measured or not; without
        # them the caller's prior alone decides whether one appears.
        tokens = (
            measured_token_forecast(request, observations)
            if observations is not None else request.token_prior
        )
        # A meter table always yields a labeled subscription block, points or not.
        subscription = None
        if meters is not None:
            try:
                subscription = subscription_forecast(request, tokens, meters)
            except ValueError as exc:
                _error(f"Subscription forecast error: {exc}", 2)
        report = replace(
            report, schema_version="agent-estimate/report/v1", tokens=tokens,
            subscription=subscription,
        )

    emit_audit_event(
        "estimation_request",
        action="estimate",
        duration_ms=(time.perf_counter() - started_at) * 1000.0,
        request={
            "input_source": input_source,
            "description_count": len(descriptions),
            "format": format,
            "review_mode": mode.value,
            "task_type": estimation_category.value if estimation_category is not None else "auto",
            "config_source": config_path.name if config_path else "default_agents.yaml",
        },
        result={
            "task_count": len(report.tasks),
            "critical_path_length": len(report.critical_path),
            "best_case_minutes": report.timeline.best_case_minutes,
            "expected_case_minutes": report.timeline.expected_case_minutes,
            "worst_case_minutes": report.timeline.worst_case_minutes,
            "review_overhead_minutes": report.review_overhead_minutes,
        },
    )

    # --- Output ---
    if format == "markdown":
        typer.echo(render_markdown_report(report, compact=compact))
    else:
        typer.echo(render_json_report(report), nl=False)


def _error(message: str, exit_code: int) -> NoReturn:
    """Print error to stderr and exit."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=exit_code)


def _spec_review_mode(request: EstimateRequest) -> ReviewMode:
    """Map the supported review plans to the existing overhead tiers."""
    review = request.execution_profile.review
    if review.mode == "none":
        return ReviewMode.NONE
    if review.expected_rounds <= 2:
        return ReviewMode(review.intensity)
    if review.expected_rounds == 3 and review.intensity == "standard":
        return ReviewMode.THREE_ROUND
    _error(
        "execution_profile.review: supported plans are none, 1–2 rounds at "
        "standard/complex intensity, or 3 standard rounds.", 2,
    )


def _spec_config(request: EstimateRequest, cfg: EstimationConfig) -> EstimationConfig:
    """Use the named configured agent; config_profile remains provenance."""
    profile = request.execution_profile
    agents = [agent for agent in cfg.agents if agent.name == profile.runtime.agent_name]
    if not agents:
        _error(
            "execution_profile.runtime.agent_name: no configured agent named "
            f"{profile.runtime.agent_name!r}; select its config with --config.", 2,
        )
    agent = agents[0]
    if profile.model.id is not None:
        agent = agent.model_copy(update={"model_tier": profile.model.id})
    return cfg.model_copy(update={"agents": [agent]})


def _fetch_github_task_descriptions(repo: str, issue_numbers: list[int]) -> list[str]:
    """Fetch GitHub issues using REST in token-only environments, otherwise gh CLI."""
    errors: list[str] = []
    if os.getenv("GITHUB_TOKEN"):
        try:
            return GitHubRestAdapter().fetch_task_descriptions_by_numbers(
                repo,
                issue_numbers,
            )
        except GitHubAdapterError as exc:
            errors.append(f"REST API: {exc}")

    try:
        return GitHubGhCliAdapter().fetch_task_descriptions_by_numbers(
            repo,
            issue_numbers,
        )
    except GitHubAdapterError as exc:
        if errors:
            errors.append(f"gh CLI: {exc}")
            raise GitHubAdapterError("; ".join(errors)) from exc
        raise


def _summarize_config_changes(
    current: EstimationConfig,
    baseline: EstimationConfig,
) -> list[str]:
    current_dump = current.model_dump()
    baseline_dump = baseline.model_dump()
    changed_fields: list[str] = []

    current_settings = current_dump["settings"]
    baseline_settings = baseline_dump["settings"]
    for key, value in current_settings.items():
        if baseline_settings.get(key) != value:
            changed_fields.append(f"settings.{key}")

    current_agents = {agent["name"]: agent for agent in current_dump["agents"]}
    baseline_agents = {agent["name"]: agent for agent in baseline_dump["agents"]}
    if set(current_agents) != set(baseline_agents):
        changed_fields.append("agents")
    else:
        for agent_name in sorted(current_agents):
            current_agent = current_agents[agent_name]
            baseline_agent = baseline_agents[agent_name]
            for field, value in current_agent.items():
                if field == "name":
                    continue
                if baseline_agent.get(field) != value:
                    changed_fields.append(f"agents.{agent_name}.{field}")

    return sorted(set(changed_fields))
