"""Read-only corpus coverage and native expected-wall scoring."""

from pathlib import Path

import typer

from agent_estimate.backtest.reader import EvidenceError, backtest, render_table


def run(
    audit_root: Path = typer.Argument(
        ..., help="OACP home/projects root or autonomy_decisions directory."
    ),
    forecasts: Path | None = typer.Option(
        None, help="Directory of retained ForecastRecord JSON/YAML files."
    ),
    receipts: Path | None = typer.Option(None, help="Directory of immutable binding receipts."),
    include_archived: bool = typer.Option(
        True, "--include-archived/--live-only", help="Include archived audit evidence."
    ),
    output_format: str = typer.Option("table", "--format", help="table or json"),
) -> None:
    """Show coverage first; score native completed segments with at least five executions."""
    import json

    if output_format not in ("table", "json"):
        raise typer.BadParameter("must be table or json", param_hint="--format")
    try:
        report = backtest(
            audit_root, forecasts=forecasts, receipts=receipts, include_archived=include_archived
        )
    except (EvidenceError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(report, indent=2, allow_nan=False)
        if output_format == "json"
        else render_table(report)
    )
