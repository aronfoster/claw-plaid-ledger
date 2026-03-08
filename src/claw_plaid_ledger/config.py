"""Environment-backed runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ as os_environ
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""

    @classmethod
    def for_missing_env_vars(cls, names: list[str]) -> ConfigError:
        """Build an error for missing environment variables."""
        vars_csv = ", ".join(names)
        message = f"Missing required environment variable(s): {vars_csv}"
        return cls(message)


@dataclass(frozen=True)
class Config:
    """Application configuration values loaded from environment variables."""

    db_path: Path
    workspace_path: Path | None
    plaid_client_id: str | None
    plaid_secret: str | None
    plaid_env: str | None
    plaid_access_token: str | None


def load_config(
    environ: dict[str, str] | None = None,
    *,
    require_plaid: bool = False,
) -> Config:
    """Load runtime configuration from environment variables."""
    values = os_environ if environ is None else environ

    db_path_raw = values.get("CLAW_PLAID_LEDGER_DB_PATH")
    workspace_raw = values.get("CLAW_PLAID_LEDGER_WORKSPACE_PATH")
    plaid_client_id = values.get("PLAID_CLIENT_ID")
    plaid_secret = values.get("PLAID_SECRET")
    plaid_env = values.get("PLAID_ENV")
    plaid_access_token = values.get("PLAID_ACCESS_TOKEN")

    missing = []
    if not db_path_raw:
        missing.append("CLAW_PLAID_LEDGER_DB_PATH")

    if require_plaid:
        if not plaid_client_id:
            missing.append("PLAID_CLIENT_ID")
        if not plaid_secret:
            missing.append("PLAID_SECRET")
        if not plaid_env:
            missing.append("PLAID_ENV")
        if not plaid_access_token:
            missing.append("PLAID_ACCESS_TOKEN")

    if missing:
        raise ConfigError.for_missing_env_vars(missing)

    if db_path_raw is None:
        raise ConfigError.for_missing_env_vars(["CLAW_PLAID_LEDGER_DB_PATH"])

    return Config(
        db_path=Path(db_path_raw).expanduser(),
        workspace_path=Path(workspace_raw).expanduser()
        if workspace_raw
        else None,
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
        plaid_access_token=plaid_access_token,
    )
