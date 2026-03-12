"""Production preflight checks for claw-plaid-ledger."""

from __future__ import annotations

import enum
import logging
import tomllib
from dataclasses import dataclass
from os import environ as os_environ
from pathlib import Path

from claw_plaid_ledger.items_config import ItemsConfigError, load_items_config

logger = logging.getLogger(__name__)


class CheckStatus(enum.Enum):
    """Outcome of a single preflight check, ordered by increasing severity."""

    # "OKAY" avoids the S105 false positive that fires when an attribute name
    # contains the word "pass" (hardcoded-password detection).
    OKAY = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class CheckSeverity(enum.Enum):
    """Whether a check failure blocks production readiness."""

    REQUIRED = "REQUIRED"
    WARNING = "WARNING"


@dataclass(frozen=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: CheckStatus
    message: str
    severity: CheckSeverity


_REQUIRED_PLAID_CLIENT_VARS: tuple[str, ...] = (
    "PLAID_CLIENT_ID",
    "PLAID_SECRET",
    "PLAID_ENV",
)


def _check_env_var(
    name: str,
    environ: dict[str, str],
    *,
    severity: CheckSeverity = CheckSeverity.REQUIRED,
) -> CheckResult:
    """Return OKAY or FAIL for a required environment variable."""
    if environ.get(name):
        return CheckResult(
            name=name,
            status=CheckStatus.OKAY,
            message=f"{name} is set",
            severity=severity,
        )
    return CheckResult(
        name=name,
        status=CheckStatus.FAIL,
        message=f"{name} is not set",
        severity=severity,
    )


def _check_db_path(environ: dict[str, str]) -> CheckResult:
    """Check that DB path is set and its parent directory is creatable."""
    name = "CLAW_PLAID_LEDGER_DB_PATH"
    raw = environ.get(name)
    if not raw:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            message=f"{name} is not set",
            severity=CheckSeverity.REQUIRED,
        )
    db_path = Path(raw).expanduser()
    if db_path.exists():
        return CheckResult(
            name=name,
            status=CheckStatus.OKAY,
            message=f"DB file exists: {db_path}",
            severity=CheckSeverity.REQUIRED,
        )
    # DB doesn't exist yet; verify an ancestor directory exists (creatable).
    ancestor = db_path.parent
    while not ancestor.exists():
        up = ancestor.parent
        if up == ancestor:
            return CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                message=(
                    f"DB path {db_path} is not creatable: "
                    "no existing parent directory found"
                ),
                severity=CheckSeverity.REQUIRED,
            )
        ancestor = up
    return CheckResult(
        name=name,
        status=CheckStatus.OKAY,
        message=(
            f"DB path {db_path} does not exist yet "
            "(run 'ledger init-db' before first sync)"
        ),
        severity=CheckSeverity.REQUIRED,
    )


def _check_sandbox_warning(environ: dict[str, str]) -> CheckResult:
    """Warn when PLAID_ENV looks like a sandbox environment."""
    plaid_env = (environ.get("PLAID_ENV") or "").lower()
    if plaid_env == "sandbox":
        return CheckResult(
            name="PLAID_ENV_SANDBOX",
            status=CheckStatus.WARN,
            message=(
                "PLAID_ENV=sandbox \u2014 this appears to be a sandbox "
                "environment, not a live production environment"
            ),
            severity=CheckSeverity.WARNING,
        )
    return CheckResult(
        name="PLAID_ENV_SANDBOX",
        status=CheckStatus.OKAY,
        message=f"PLAID_ENV={plaid_env!r} (not sandbox)",
        severity=CheckSeverity.WARNING,
    )


def _check_item_token(
    item_id: str,
    access_token_env: str,
    environ: dict[str, str],
) -> CheckResult:
    """Return OKAY or FAIL for one item's access-token env var."""
    if environ.get(access_token_env):
        return CheckResult(
            name=access_token_env,
            status=CheckStatus.OKAY,
            message=f"{access_token_env} is set (item={item_id!r})",
            severity=CheckSeverity.REQUIRED,
        )
    return CheckResult(
        name=access_token_env,
        status=CheckStatus.FAIL,
        message=(
            f"{access_token_env} is not set (required for item={item_id!r})"
        ),
        severity=CheckSeverity.REQUIRED,
    )


def _check_items_config(
    items_config_path: Path | None,
    environ: dict[str, str],
) -> list[CheckResult]:
    """Check items.toml parseability and per-item access-token env vars."""
    results: list[CheckResult] = []
    try:
        items = load_items_config(items_config_path)
    except (ItemsConfigError, tomllib.TOMLDecodeError) as error:
        results.append(
            CheckResult(
                name="items.toml",
                status=CheckStatus.FAIL,
                message=f"items.toml parse error: {error}",
                severity=CheckSeverity.REQUIRED,
            )
        )
        return results

    if not items:
        results.append(
            CheckResult(
                name="items.toml",
                status=CheckStatus.OKAY,
                message=(
                    "items.toml not found or empty \u2014 single-item mode"
                ),
                severity=CheckSeverity.WARNING,
            )
        )
        return results

    results.append(
        CheckResult(
            name="items.toml",
            status=CheckStatus.OKAY,
            message=f"items.toml loaded: {len(items)} item(s)",
            severity=CheckSeverity.REQUIRED,
        )
    )
    results.extend(
        _check_item_token(item.id, item.access_token_env, environ)
        for item in items
    )
    return results


def run_production_preflight(
    environ: dict[str, str] | None = None,
    *,
    items_config_path: Path | None = None,
) -> list[CheckResult]:
    """Run all production preflight checks and return results."""
    env = dict(os_environ if environ is None else environ)
    results: list[CheckResult] = [
        _check_env_var(var, env) for var in _REQUIRED_PLAID_CLIENT_VARS
    ]
    results.append(_check_env_var("CLAW_API_SECRET", env))
    results.append(_check_db_path(env))
    results.extend(_check_items_config(items_config_path, env))
    results.append(_check_sandbox_warning(env))
    for result in results:
        logger.debug(
            "preflight check %s [%s]: %s",
            result.name,
            result.status.value,
            result.message,
        )
    return results
