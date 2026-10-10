"""Smoke tests for package metadata."""

from agent_estimate import __version__


def test_version_string_present() -> None:
    assert __version__ == "0.9.0"
