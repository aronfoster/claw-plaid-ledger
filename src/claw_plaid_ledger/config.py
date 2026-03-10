"""Environment-backed runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ as os_environ
from pathlib import Path

_VALID_LOG_LEVELS = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""

    @classmethod
    def for_missing_env_vars(cls, names: list[str]) -> ConfigError:
        """Build an error for missing environment variables."""
        vars_csv = ", ".join(names)
        message = f"Missing required environment variable(s): {vars_csv}"
        return cls(message)


DEFAULT_ITEM_ID = "default-item"


def _missing_required_vars(
    values: dict[str, str | None],
    *,
    require_plaid: bool,
    require_plaid_client: bool,
) -> list[str]:
    """Return names of required environment variables that are missing."""
    missing: list[str] = []
    if not values["CLAW_PLAID_LEDGER_DB_PATH"]:
        missing.append("CLAW_PLAID_LEDGER_DB_PATH")

    if require_plaid or require_plaid_client:
        if not values["PLAID_CLIENT_ID"]:
            missing.append("PLAID_CLIENT_ID")
        if not values["PLAID_SECRET"]:
            missing.append("PLAID_SECRET")
        if not values["PLAID_ENV"]:
            missing.append("PLAID_ENV")

    if require_plaid and not values["PLAID_ACCESS_TOKEN"]:
        missing.append("PLAID_ACCESS_TOKEN")

    return missing


@dataclass(frozen=True)
class OpenClawConfig:
    """OpenClaw notification endpoint configuration."""

    url: str
    token: str | None
    agent: str
    wake_mode: str


@dataclass(frozen=True)
class Config:
    """Application configuration values loaded from environment variables."""

    db_path: Path
    workspace_path: Path | None
    plaid_client_id: str | None
    plaid_secret: str | None
    plaid_env: str | None
    plaid_access_token: str | None
    api_secret: str | None = None
    plaid_webhook_secret: str | None = None
    item_id: str = DEFAULT_ITEM_ID
    log_level: str = "INFO"
    openclaw_hooks_url: str = "http://127.0.0.1:18789/hooks/agent"
    openclaw_hooks_token: str | None = None
    openclaw_hooks_agent: str = "Hestia"
    openclaw_hooks_wake_mode: str = "now"


def _default_env_file() -> Path:
    """Return the default per-user environment file path."""
    return Path("~/.config/claw-plaid-ledger/.env").expanduser()


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple .env file into key/value pairs."""
    if not path.exists():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, sep, value = line.partition("=")
        if not sep:
            continue

        parsed[key.strip()] = value.strip().strip("\"'")

    return parsed


def load_config(
    environ: dict[str, str] | None = None,
    *,
    require_plaid: bool = False,
    require_plaid_client: bool = False,
    env_file: Path | None = None,
) -> Config:
    """Load runtime configuration from env vars and optional .env file."""
    candidate_env_file = _default_env_file() if env_file is None else env_file
    file_values = _load_env_file(candidate_env_file)
    runtime_values = dict(os_environ if environ is None else environ)
    values = {**file_values, **runtime_values}

    db_path_raw = values.get("CLAW_PLAID_LEDGER_DB_PATH")
    workspace_raw = values.get("CLAW_PLAID_LEDGER_WORKSPACE_PATH")
    plaid_client_id = values.get("PLAID_CLIENT_ID")
    plaid_secret = values.get("PLAID_SECRET")
    plaid_env = values.get("PLAID_ENV")
    plaid_access_token = values.get("PLAID_ACCESS_TOKEN")
    api_secret = values.get("CLAW_API_SECRET") or None
    plaid_webhook_secret = values.get("PLAID_WEBHOOK_SECRET") or None
    item_id = values.get("CLAW_PLAID_LEDGER_ITEM_ID") or DEFAULT_ITEM_ID
    openclaw_hooks_url = (
        values.get("OPENCLAW_HOOKS_URL")
        or "http://127.0.0.1:18789/hooks/agent"
    )
    openclaw_hooks_token = values.get("OPENCLAW_HOOKS_TOKEN") or None
    openclaw_hooks_agent = values.get("OPENCLAW_HOOKS_AGENT") or "Hestia"
    openclaw_hooks_wake_mode = values.get("OPENCLAW_HOOKS_WAKE_MODE") or "now"
    log_level_raw = (values.get("CLAW_LOG_LEVEL") or "INFO").upper()
    if log_level_raw not in _VALID_LOG_LEVELS:
        valid_names = ", ".join(sorted(_VALID_LOG_LEVELS))
        msg = (
            f"Invalid CLAW_LOG_LEVEL: {log_level_raw!r}."
            f" Must be one of: {valid_names}"
        )
        raise ConfigError(msg)

    missing = _missing_required_vars(
        {
            "CLAW_PLAID_LEDGER_DB_PATH": db_path_raw,
            "PLAID_CLIENT_ID": plaid_client_id,
            "PLAID_SECRET": plaid_secret,
            "PLAID_ENV": plaid_env,
            "PLAID_ACCESS_TOKEN": plaid_access_token,
        },
        require_plaid=require_plaid,
        require_plaid_client=require_plaid_client,
    )
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
        api_secret=api_secret,
        plaid_webhook_secret=plaid_webhook_secret,
        item_id=item_id,
        log_level=log_level_raw,
        openclaw_hooks_url=openclaw_hooks_url,
        openclaw_hooks_token=openclaw_hooks_token,
        openclaw_hooks_agent=openclaw_hooks_agent,
        openclaw_hooks_wake_mode=openclaw_hooks_wake_mode,
    )
