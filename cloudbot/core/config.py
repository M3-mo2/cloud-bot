"""``Settings`` — loaded from environment / ``.env`` via ``pydantic-settings``.

Immutable, validated at startup, injected everywhere. No secret is ever logged
or rendered: secrets are ``SecretStr`` and the logging setup redacts them.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cloudbot.core.locale import DEFAULT_LOCALE, Locale, coerce_locale
from cloudbot.core.modes import RunMode
from cloudbot.core.modes import log_level as derive_log_level


class ConfigError(Exception):
    """A fatal configuration error; the bot refuses to start."""


def _parse_ids(value: object) -> frozenset[int]:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, (set, frozenset, list, tuple)):
        return frozenset(int(v) for v in value)
    return frozenset(int(part.strip()) for part in str(value).split(",") if part.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLOUDBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    mode: RunMode = RunMode.PRODUCTION
    bot_token: SecretStr = SecretStr("")
    engine_base_url: str = "http://127.0.0.1:8098"
    engine_api_key: SecretStr = SecretStr("")
    engine_timeout_s: float = 30.0
    engine_max_retries: int = 2
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    admin_ids: frozenset[int] = Field(default_factory=frozenset)
    default_locale: Locale = DEFAULT_LOCALE
    throttle_rate: float = 5.0
    log_level: str = ""
    debug_show_raw: bool = False
    mock_engine: bool = False
    #: The in-bot developer tools (``/dev``). OFF by default: the tool can render
    #: every screen and inspect every live button, so it must be opted into
    #: explicitly. It is additionally admin-gated (``is_admin``) — see
    #: ``middleware/admin_gate.py`` — so it can never be reached by a non-admin.
    dev_tools: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def _validate_mode(cls, value: object) -> RunMode:
        if isinstance(value, RunMode):
            return value
        try:
            return RunMode(str(value).lower())
        except ValueError as exc:  # noqa: TRY003
            allowed = ", ".join(m.value for m in RunMode)
            raise ValueError(f"unknown mode {value!r}; expected one of: {allowed}") from exc

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _validate_admin_ids(cls, value: object) -> frozenset[int]:
        return _parse_ids(value)

    @field_validator("default_locale", mode="before")
    @classmethod
    def _validate_locale(cls, value: object) -> Locale:
        return coerce_locale(value)

    @model_validator(mode="after")
    def _guard_debug(self) -> "Settings":
        if self.mode is RunMode.DEBUG and not self.admin_ids:
            raise ValueError(
                "DEBUG mode requires a non-empty CLOUDBOT_ADMIN_IDS (it would be a footgun)"
            )
        return self

    @property
    def effective_log_level(self) -> int:
        if self.log_level:
            import logging

            level = logging.getLevelName(self.log_level.upper())
            if isinstance(level, int):
                return level
        return derive_log_level(self.mode)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
