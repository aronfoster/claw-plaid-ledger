"""Unit tests for production preflight check logic."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import pytest

from claw_plaid_ledger.preflight import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    run_production_preflight,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_base_env(tmp_path: Path) -> dict[str, str]:
    """Return a minimal valid production environment dict."""
    return {
        "PLAID_CLIENT_ID": "client-id",
        "PLAID_SECRET": "plaid-secret",
        "PLAID_ENV": "production",
        "CLAW_API_SECRET": "api-secret",
        "CLAW_PLAID_LEDGER_DB_PATH": str(tmp_path / "ledger.db"),
    }


def _get_result(results: list[CheckResult], name: str) -> CheckResult:
    """Extract a check result by name, raising if not found."""
    for result in results:
        if result.name == name:
            return result
    msg = f"No check result with name {name!r}"
    raise AssertionError(msg)


def test_all_required_checks_pass_with_valid_env(tmp_path: Path) -> None:
    """All required checks pass when environment is fully configured."""
    env = _make_base_env(tmp_path)

    results = run_production_preflight(
        env, items_config_path=tmp_path / "items.toml"
    )

    failures = [
        r
        for r in results
        if r.status is CheckStatus.FAIL
        and r.severity is CheckSeverity.REQUIRED
    ]
    assert failures == []


@pytest.mark.parametrize(
    "missing_var",
    ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV", "CLAW_API_SECRET"],
)
def test_missing_required_plaid_or_api_var_is_hard_fail(
    tmp_path: Path,
    missing_var: str,
) -> None:
    """Each required Plaid/API variable missing is a required hard failure."""
    env = _make_base_env(tmp_path)
    del env[missing_var]

    results = run_production_preflight(env)

    result = _get_result(results, missing_var)
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED


def test_missing_db_path_env_var_is_required_fail(tmp_path: Path) -> None:
    """CLAW_PLAID_LEDGER_DB_PATH missing is a required hard failure."""
    env = _make_base_env(tmp_path)
    del env["CLAW_PLAID_LEDGER_DB_PATH"]

    results = run_production_preflight(env)

    result = _get_result(results, "CLAW_PLAID_LEDGER_DB_PATH")
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED


def test_db_path_creatable_parent_is_pass(tmp_path: Path) -> None:
    """DB path with existing parent directory passes even if file is absent."""
    env = _make_base_env(tmp_path)
    assert not (tmp_path / "ledger.db").exists()

    results = run_production_preflight(env)

    result = _get_result(results, "CLAW_PLAID_LEDGER_DB_PATH")
    assert result.status is CheckStatus.OKAY


def test_db_path_existing_file_is_pass(tmp_path: Path) -> None:
    """Existing DB file path is a pass."""
    db_path = tmp_path / "ledger.db"
    db_path.touch()
    env = _make_base_env(tmp_path)

    results = run_production_preflight(env)

    result = _get_result(results, "CLAW_PLAID_LEDGER_DB_PATH")
    assert result.status is CheckStatus.OKAY


def test_sandbox_env_is_warning_not_required_fail(tmp_path: Path) -> None:
    """PLAID_ENV=sandbox triggers a WARNING, not a required failure."""
    env = _make_base_env(tmp_path)
    env["PLAID_ENV"] = "sandbox"

    results = run_production_preflight(env)

    result = _get_result(results, "PLAID_ENV_SANDBOX")
    assert result.status is CheckStatus.WARN
    assert result.severity is CheckSeverity.WARNING


def test_sandbox_warning_does_not_block_required_pass(
    tmp_path: Path,
) -> None:
    """Sandbox warning alone does not create any required failures."""
    env = _make_base_env(tmp_path)
    env["PLAID_ENV"] = "sandbox"

    results = run_production_preflight(
        env, items_config_path=tmp_path / "items.toml"
    )

    required_failures = [
        r
        for r in results
        if r.status is CheckStatus.FAIL
        and r.severity is CheckSeverity.REQUIRED
    ]
    assert required_failures == []


def test_production_plaid_env_has_no_sandbox_warning(
    tmp_path: Path,
) -> None:
    """PLAID_ENV=production passes the sandbox check."""
    env = _make_base_env(tmp_path)

    results = run_production_preflight(env)

    result = _get_result(results, "PLAID_ENV_SANDBOX")
    assert result.status is CheckStatus.OKAY


def test_items_toml_parse_error_is_required_fail(tmp_path: Path) -> None:
    """Malformed items.toml is a required hard failure in preflight mode."""
    items_path = tmp_path / "items.toml"
    # Valid TOML but missing required 'id' field — triggers ItemsConfigError.
    items_path.write_text('[[items]]\naccess_token_env = "PLAID_TOKEN"\n')
    env = _make_base_env(tmp_path)

    results = run_production_preflight(env, items_config_path=items_path)

    result = _get_result(results, "items.toml")
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED


def test_invalid_toml_syntax_is_required_fail(tmp_path: Path) -> None:
    """Invalid TOML syntax in items.toml is a required hard failure."""
    items_path = tmp_path / "items.toml"
    items_path.write_text("[[ invalid toml\n")
    env = _make_base_env(tmp_path)

    results = run_production_preflight(env, items_config_path=items_path)

    result = _get_result(results, "items.toml")
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED


def test_missing_access_token_env_var_is_required_fail(
    tmp_path: Path,
) -> None:
    """Missing access token env var referenced by items.toml is a hard fail."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        '[[items]]\nid = "bank"\naccess_token_env = "PLAID_TOKEN_BANK"\n'
    )
    env = _make_base_env(tmp_path)
    # PLAID_TOKEN_BANK deliberately absent

    results = run_production_preflight(env, items_config_path=items_path)

    result = _get_result(results, "PLAID_TOKEN_BANK")
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED
    assert "bank" in result.message


def test_present_access_token_env_var_is_pass(tmp_path: Path) -> None:
    """Access token env var present in environment is a pass."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        '[[items]]\nid = "bank"\naccess_token_env = "PLAID_TOKEN_BANK"\n'
    )
    env = _make_base_env(tmp_path)
    env["PLAID_TOKEN_BANK"] = secrets.token_urlsafe(16)

    results = run_production_preflight(env, items_config_path=items_path)

    result = _get_result(results, "PLAID_TOKEN_BANK")
    assert result.status is CheckStatus.OKAY
    assert result.severity is CheckSeverity.REQUIRED


def test_absent_items_toml_is_not_a_failure(tmp_path: Path) -> None:
    """Absent items.toml is reported as a pass (single-item mode note)."""
    absent_path = tmp_path / "no-items.toml"
    env = _make_base_env(tmp_path)

    results = run_production_preflight(env, items_config_path=absent_path)

    result = _get_result(results, "items.toml")
    assert result.status is CheckStatus.OKAY


def test_multiple_items_all_tokens_present_all_pass(
    tmp_path: Path,
) -> None:
    """All items with present env vars produce all-pass token checks."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        "[[items]]\n"
        'id = "bank-alice"\n'
        'access_token_env = "PLAID_TOKEN_ALICE"\n'
        "\n"
        "[[items]]\n"
        'id = "bank-bob"\n'
        'access_token_env = "PLAID_TOKEN_BOB"\n'
    )
    env = _make_base_env(tmp_path)
    env["PLAID_TOKEN_ALICE"] = secrets.token_urlsafe(16)
    env["PLAID_TOKEN_BOB"] = secrets.token_urlsafe(16)

    results = run_production_preflight(env, items_config_path=items_path)

    assert _get_result(results, "PLAID_TOKEN_ALICE").status is CheckStatus.OKAY
    assert _get_result(results, "PLAID_TOKEN_BOB").status is CheckStatus.OKAY


def test_partial_items_token_missing_yields_fail(tmp_path: Path) -> None:
    """One missing token among multiple items is a targeted hard failure."""
    items_path = tmp_path / "items.toml"
    items_path.write_text(
        "[[items]]\n"
        'id = "bank-alice"\n'
        'access_token_env = "PLAID_TOKEN_ALICE"\n'
        "\n"
        "[[items]]\n"
        'id = "bank-bob"\n'
        'access_token_env = "PLAID_TOKEN_BOB"\n'
    )
    env = _make_base_env(tmp_path)
    env["PLAID_TOKEN_ALICE"] = secrets.token_urlsafe(16)
    # PLAID_TOKEN_BOB deliberately absent

    results = run_production_preflight(env, items_config_path=items_path)

    assert _get_result(results, "PLAID_TOKEN_ALICE").status is CheckStatus.OKAY
    assert _get_result(results, "PLAID_TOKEN_BOB").status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# Webhook allowlist preflight checks
# ---------------------------------------------------------------------------


def test_webhook_allowlist_absent_is_warning(tmp_path: Path) -> None:
    """Missing CLAW_WEBHOOK_ALLOWED_IPS is a WARNING, not a hard failure."""
    env = _make_base_env(tmp_path)
    # CLAW_WEBHOOK_ALLOWED_IPS deliberately absent

    results = run_production_preflight(env)

    result = _get_result(results, "webhook-allowlist")
    assert result.status is CheckStatus.WARN
    assert result.severity is CheckSeverity.WARNING
    assert "reachable from any source IP" in result.message


def test_webhook_allowlist_configured_is_okay(tmp_path: Path) -> None:
    """Valid CLAW_WEBHOOK_ALLOWED_IPS reports OKAY with CIDR count."""
    env = _make_base_env(tmp_path)
    env["CLAW_WEBHOOK_ALLOWED_IPS"] = "52.21.0.0/16,3.211.0.0/16"

    results = run_production_preflight(env)

    result = _get_result(results, "webhook-allowlist")
    assert result.status is CheckStatus.OKAY
    assert "2 CIDR" in result.message


def test_webhook_allowlist_invalid_cidr_is_required_fail(
    tmp_path: Path,
) -> None:
    """Invalid CIDR in CLAW_WEBHOOK_ALLOWED_IPS is a required hard failure."""
    env = _make_base_env(tmp_path)
    env["CLAW_WEBHOOK_ALLOWED_IPS"] = "not-a-cidr"

    results = run_production_preflight(env)

    result = _get_result(results, "webhook-allowlist")
    assert result.status is CheckStatus.FAIL
    assert result.severity is CheckSeverity.REQUIRED
