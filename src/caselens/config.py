"""JSON configuration with private inline or named environment credentials."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from caselens.schemas import Contract


class Limits(Contract):
    concurrency: int = Field(default=4, ge=1, le=32)
    requests_per_minute: float = Field(default=60, ge=0)
    timeout_seconds: float = Field(default=60, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=3)
    backoff_seconds: float = Field(default=.5, ge=0)
    circuit_failures: int = Field(default=5, ge=1)
    circuit_cooldown_seconds: float = Field(default=30, gt=0)


class EndpointConfig(Contract):
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    api_key_env: str = "VLM_API_KEY"
    structured_mode: str = "json_schema"
    limits: Limits = Field(default_factory=Limits)

    @field_validator("api_key_env")
    @classmethod
    def explicit_key_variable(cls, value):
        if value not in {"VLM_API_KEY", "CONTROLLER_API_KEY", "TYPESAFE_API_KEY"}:
            raise ValueError("api_key_env must be VLM_API_KEY, CONTROLLER_API_KEY or TYPESAFE_API_KEY")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value):
        if value is None:
            return value
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base_url must be an HTTP API root without credentials, query or fragment")
        hostname = parts.hostname.strip("[]")
        if parts.scheme != "https" and hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("remote API endpoints must use HTTPS")
        return value.rstrip("/")

    @field_validator("structured_mode")
    @classmethod
    def supported_mode(cls, value):
        if value not in {"json_schema", "json_object"}:
            raise ValueError("structured_mode must be json_schema or json_object")
        return value

    def key(self) -> str:
        if self.api_key is not None:
            value = self.api_key.get_secret_value().strip()
            if value:
                return value
        value = os.environ.get(self.api_key_env, "").strip()
        if not value:
            raise ValueError(f"fill api_key in JSON or set environment variable {self.api_key_env}")
        return value

    def key_source(self) -> str:
        if self.api_key is not None and self.api_key.get_secret_value().strip():
            return "config"
        return "environment" if os.environ.get(self.api_key_env, "").strip() else "missing"


class JevConfig(EndpointConfig):
    api_key_env: str = "TYPESAFE_API_KEY"
    limits: Limits = Field(default_factory=lambda: Limits(timeout_seconds=15))


class Settings(Contract):
    vlm: EndpointConfig = Field(default_factory=EndpointConfig)
    controller: EndpointConfig | None = None
    jev: JevConfig = Field(default_factory=JevConfig)
    max_rounds: int = Field(default=6, ge=1, le=6)
    max_supplements: int = Field(default=2, ge=0, le=2)
    threshold: float = Field(default=.8, ge=0, le=1)
    max_calls_per_round: int = Field(default=8, ge=1, le=16)
    context_max_input_tokens: int = Field(default=24000, ge=1000, le=200000)
    context_output_reserve: int = Field(default=1200, ge=100, le=50000)
    context_recent_groups: int = Field(default=3, ge=0, le=20)
    state_dir: Path = Path(".caselens")

    @classmethod
    def load(cls, path: Path | None = None):
        settings = cls.model_validate_json(path.read_text(encoding="utf-8")) if path else cls()
        for role in ("vlm", "controller", "jev"):
            endpoint = getattr(settings, role)
            if endpoint is None:
                continue
            updates = {}
            for field in ("base_url", "model"):
                value = os.environ.get(f"CASELENS_{role.upper()}_{field.upper()}")
                if value:
                    updates[field] = value
            if updates:
                setattr(settings, role, type(endpoint).model_validate({**endpoint.model_dump(), "api_key": endpoint.api_key, **updates}))
        return settings

    def validate_live(self):
        for name, endpoint in [("vlm", self.vlm), ("controller", self.controller or self.vlm)]:
            if not endpoint.base_url or not endpoint.model:
                raise ValueError(f"{name}.base_url and {name}.model must be configured")
            endpoint.key()
        self.jev.key()
