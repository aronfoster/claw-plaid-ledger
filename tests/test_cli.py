"""CLI smoke tests."""

from __future__ import annotations

from typer.testing import CliRunner

from claw_plaid_ledger.cli import app

runner = CliRunner()


def test_doctor_default() -> None:
    """`doctor` command returns the baseline setup status."""
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "doctor: basic checks passed" in result.stdout


def test_doctor_verbose() -> None:
    """`doctor --verbose` returns the verbose placeholder status."""
    result = runner.invoke(app, ["doctor", "--verbose"])

    assert result.exit_code == 0
    assert "doctor: verbose diagnostics not implemented yet" in result.stdout


def test_help() -> None:
    """CLI help is available via Typer."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Local-first finance ledger CLI" in result.stdout
