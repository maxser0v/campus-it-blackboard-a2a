"""Environment-backed configuration for local services."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    env: str = "local"
    database_path: str = ".data/blackboard.sqlite3"
    bind_host: str = "127.0.0.1"
    blackboard_port: int = 8100
    controller_port: int = 8101
    agent_urls: Mapping[str, str] = field(default_factory=dict)
    agent_credentials: Mapping[str, str] = field(default_factory=dict)
    model_mode: str = "offline"
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: str | None = None
    model_timeout_seconds: float = 60.0
    model_max_output_tokens: int = 1600
    model_token_limit_parameter: str | None = None
    request_timeout: float = 120.0
    max_dispatches: int = 12
    max_retries: int = 1
    max_run_tokens: int = 12000
    case_timeout_seconds: int = 300

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "Settings":
        if load_dotenv_file:
            load_dotenv(override=False)

        raw_credentials = os.getenv("CAMPUS_AGENT_CREDENTIALS_JSON", "{}")
        try:
            credentials = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise ValueError("CAMPUS_AGENT_CREDENTIALS_JSON must be valid JSON") from exc
        if not isinstance(credentials, dict) or any(
            not isinstance(actor, str) or not isinstance(token, str) or not token
            for actor, token in credentials.items()
        ):
            raise ValueError("CAMPUS_AGENT_CREDENTIALS_JSON must map actor names to non-empty tokens")

        urls = {
            actor: os.getenv(f"CAMPUS_{actor.upper()}_URL", "").rstrip("/")
            for actor in ("blackboard", "controller", "evidence", "diagnosis", "review", "response")
        }
        urls = {actor: url for actor, url in urls.items() if url}
        return cls(
            env=os.getenv("CAMPUS_ENV", "local"),
            database_path=os.getenv("CAMPUS_DATABASE_PATH", ".data/blackboard.sqlite3"),
            bind_host=os.getenv("CAMPUS_BIND_HOST", "127.0.0.1"),
            blackboard_port=_int_env("CAMPUS_BLACKBOARD_PORT", 8100),
            controller_port=_int_env("CAMPUS_CONTROLLER_PORT", 8101),
            agent_urls=urls,
            agent_credentials=credentials,
            model_mode=os.getenv("CAMPUS_MODEL_MODE", "offline").lower(),
            model_base_url=_optional_env("CAMPUS_MODEL_BASE_URL"),
            model_name=_optional_env("CAMPUS_MODEL_NAME"),
            model_api_key=_optional_env("CAMPUS_MODEL_API_KEY"),
            model_timeout_seconds=_float_env("CAMPUS_MODEL_TIMEOUT_SECONDS", 60.0),
            model_max_output_tokens=_int_env("CAMPUS_MODEL_MAX_OUTPUT_TOKENS", 1600),
            model_token_limit_parameter=_optional_env("CAMPUS_MODEL_TOKEN_LIMIT_PARAMETER"),
            request_timeout=_float_env("CAMPUS_REQUEST_TIMEOUT", 120.0),
            max_dispatches=_int_env("CAMPUS_MAX_DISPATCHES", 12),
            max_retries=_int_env("CAMPUS_MAX_RETRIES", 1),
            max_run_tokens=_int_env("CAMPUS_MAX_RUN_TOKENS", 12000),
            case_timeout_seconds=_int_env("CAMPUS_CASE_TIMEOUT_SECONDS", 300),
        )

    def __post_init__(self) -> None:
        if self.model_mode not in {"offline", "live"}:
            raise ValueError("model_mode must be 'offline' or 'live'")
        if self.request_timeout <= 0 or self.max_dispatches < 1 or self.max_run_tokens < 1 or self.case_timeout_seconds < 1:
            raise ValueError("timeouts and dispatch limits must be positive")
        if self.model_timeout_seconds <= 0 or self.model_max_output_tokens < 1:
            raise ValueError("model timeout and output-token limit must be positive")
        if not 1 <= self.blackboard_port <= 65535 or not 1 <= self.controller_port <= 65535:
            raise ValueError("service ports must be between 1 and 65535")
        if any(not actor.strip() or not token for actor, token in self.agent_credentials.items()):
            raise ValueError("agent credential actor names and tokens must be non-empty")
        if len(set(self.agent_credentials.values())) != len(self.agent_credentials):
            raise ValueError("each actor must have a unique A2A credential")
        if self.model_mode == "live" and not (self.model_base_url and self.model_name):
            raise ValueError("live model mode requires model_base_url and model_name")
        if self.model_token_limit_parameter not in {None, "max_tokens", "max_completion_tokens"}:
            raise ValueError("model_token_limit_parameter must be max_tokens, max_completion_tokens, or unset")
        if Path(self.database_path).name in {"", ".", ".."}:
            raise ValueError("database_path must name a database file")


def get_settings() -> Settings:
    """Load settings from environment and an optional local ``.env`` file."""

    return Settings.from_env()


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc

