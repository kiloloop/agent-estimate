"""Typer application entrypoint."""

import logging

import typer

from agent_estimate.audit import configure_audit_logger
from agent_estimate.cli.commands.backtest import run as run_backtest
from agent_estimate.cli.commands.calibrate import run as run_calibrate
from agent_estimate.cli.commands.estimate import run as run_estimate
from agent_estimate.cli.commands.session import run as run_session
from agent_estimate.cli.commands.validate import run as run_validate
from agent_estimate.version import __version__

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Estimate AI-agent delivery time using PERT, METR thresholds, and wave planning.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-estimate {__version__}")
        raise typer.Exit()


@app.callback()
def _global_options(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Global options for agent-estimate."""
    configure_audit_logger()
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s", force=True)


app.command("backtest")(run_backtest)
app.command("estimate")(run_estimate)
app.command("calibrate")(run_calibrate)
app.command("validate")(run_validate)
app.command("session")(run_session)


def main() -> None:
    """Run the CLI app."""
    app()


if __name__ == "__main__":
    main()
