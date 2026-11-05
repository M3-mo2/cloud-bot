"""Engine error code → human screen (the no-raw-output guarantee).

Every engine ``error.code`` maps to a title key, a sentence key, and action
buttons. An unknown code falls back to the safe generic, logged, and the CI
exhaustiveness test fails so a human adds a sentence. Nothing raw reaches a user.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from cloudbot.core.errors import (
    DomainError,
    EngineRefused,
    EngineUnavailable,
    InvalidInput,
    NotOwned,
    RateLimited,
    WorkspaceMissing,
)
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode, shows_raw_payloads
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.components import RawPayloadDetails
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.screen import Screen

log = structlog.get_logger("cloudbot.mapping")

#: code → (title_key, message_key). Every code in the adapter vocabulary.
ERROR_KEYS: dict[str, tuple[str, str]] = {
    "validation": ("err.validation.title", "err.validation"),
    "unauthorized": ("err.unauthorized.title", "err.unauthorized"),
    "forbidden": ("err.forbidden.title", "err.forbidden"),
    "subscription_expired": ("err.subscription_expired.title", "err.subscription_expired"),
    "not_found": ("err.not_found.title", "err.not_found"),
    "ports_disabled": ("err.ports_disabled.title", "err.ports_disabled"),
    "conflict": ("err.conflict.title", "err.conflict"),
    "workspace_suspended": ("err.workspace_suspended.title", "err.workspace_suspended"),
    "too_many_sessions": ("err.too_many_sessions.title", "err.too_many_sessions"),
    "session_ended": ("err.session_ended.title", "err.session_ended"),
    "archive_entry_exists": ("err.archive_entry_exists.title", "err.archive_entry_exists"),
    "payload_too_large": ("err.payload_too_large.title", "err.payload_too_large"),
    "archive_too_large": ("err.archive_too_large.title", "err.archive_too_large"),
    "archive_too_many_entries": (
        "err.archive_too_many_entries.title",
        "err.archive_too_many_entries",
    ),
    "archive_ratio_exceeded": ("err.archive_ratio_exceeded.title", "err.archive_ratio_exceeded"),
    "unsupported_archive": ("err.unsupported_archive.title", "err.unsupported_archive"),
    "unprocessable": ("err.unprocessable.title", "err.unprocessable"),
    "unknown_profile": ("err.unknown_profile.title", "err.unknown_profile"),
    "not_directory": ("err.not_directory.title", "err.not_directory"),
    "session_needs_interpreter": (
        "err.session_needs_interpreter.title",
        "err.session_needs_interpreter",
    ),
    "subscription_invalid": ("err.subscription_invalid.title", "err.subscription_invalid"),
    "max_duration_exceeded": ("err.max_duration_exceeded.title", "err.max_duration_exceeded"),
    "subscription_would_shorten": (
        "err.subscription_would_shorten.title",
        "err.subscription_would_shorten",
    ),
    "tunnels_disabled": ("err.tunnels_disabled.title", "err.tunnels_disabled"),
    "too_many_tunnels": ("err.too_many_tunnels.title", "err.too_many_tunnels"),
    "archive_encrypted": ("err.archive_encrypted.title", "err.archive_encrypted"),
    "archive_multivolume": ("err.archive_multivolume.title", "err.archive_multivolume"),
    "archive_unsafe_entry": ("err.archive_unsafe_entry.title", "err.archive_unsafe_entry"),
    "write_rate_exceeded": ("err.write_rate_exceeded.title", "err.write_rate_exceeded"),
    "internal": ("err.internal.title", "err.internal"),
    "unavailable": ("err.unavailable.title", "err.unavailable"),
    "tunnel_url_unavailable": ("err.tunnel_url_unavailable.title", "err.tunnel_url_unavailable"),
    "insufficient_storage": ("err.insufficient_storage.title", "err.insufficient_storage"),
    "timeout": ("err.timeout.title", "err.timeout"),
    "unknown": ("err.unknown.title", "err.unknown"),
}

_RETRY_CODES = frozenset({"conflict", "unprocessable", "write_rate_exceeded", "internal", "unavailable", "tunnel_url_unavailable", "timeout", "validation"})


@dataclass(frozen=True)
class ErrorView:
    code: str
    title_key: str
    message_key: str
    retryable: bool
    extra_actions: tuple[tuple[str, str, Intent], ...] = ()


def _unprocessable_message_key(details: dict) -> str:
    field = str(details.get("field", "") or "").lower()
    if field in ("no_timeout", "allow_unlimited_runs"):
        return "err.unprocessable.no_timeout"
    if field in ("start_line", "end_line", "range", "offset", "limit"):
        return "err.unprocessable.range"
    if field == "signal":
        return "err.unprocessable.signal"
    if field == "package":
        return "err.domain.invalid_input.package_invalid"
    if field == "port":
        return "err.domain.invalid_input.port_out_of_range"
    if field in (
        "cpu_cores",
        "memory_mb",
        "disk_mb",
        "max_wall_time_sec",
        "max_runs",
        "limit",
    ):
        return "err.unprocessable.limit"
    return "err.unprocessable.default"


def _tunnels_disabled_message_key(details: dict) -> str:
    """Which of the three tunnel switches is off, from ``details.config``/``field``.

    The engine's ``422 tunnels_disabled`` names the switch that failed; each has
    its own sentence and, where it belongs, its own fix. When the engine is silent
    about which switch, the generic sentence is used.
    """
    field = str(details.get("field", "") or "").lower()
    config = str(details.get("config", "") or "").lower()
    if field == "allow_tunnels" or config.endswith("allow_tunnels"):
        return "err.tunnels_disabled.allow_tunnels"
    if field == "allow_net" or config.endswith("allow_net"):
        return "err.tunnels_disabled.allow_net"
    if "tunnels.enabled" in config or field == "enabled":
        return "err.tunnels_disabled.server"
    return "err.tunnels_disabled"


def tunnels_disabled_message(details: dict, locale: Locale) -> str:
    """The specific ``tunnels_disabled`` sentence for the engine's own details.

    The tunnel publish path renders this so a gate refusal names the switch the
    **engine** reported, never the bot's possibly-stale cached limits (F-05).
    """
    return catalog.t(_tunnels_disabled_message_key(details), locale)


def _extra_actions_for(code: str) -> tuple[tuple[str, str, Intent], ...]:
    if code == "workspace_suspended":
        return (("err.resume", "ws:resume", Intent.SUCCESS),)
    if code == "subscription_expired":
        return (("err.renew", "ws:renew", Intent.SUCCESS),)
    if code == "unknown_profile":
        return (("err.choose_runtime", "nav:profiles", Intent.SUCCESS),)
    if code == "insufficient_storage":
        return (("err.manage_files", "nav:manage_files", Intent.PRIMARY),)
    if code == "too_many_sessions":
        return (("err.my_terminals", "nav:terminals", Intent.PRIMARY),)
    if code == "session_ended":
        return (("err.new_terminal", "nav:new_terminal", Intent.SUCCESS),)
    return ()


def view_for_error(err: DomainError) -> ErrorView:
    if isinstance(err, EngineRefused):
        code = err.engine_code if err.engine_code in ERROR_KEYS else "unknown"
        if code == "unknown":
            log.warning("error.unknown_engine_code", code=err.engine_code)
        title_key, message_key = ERROR_KEYS[code]
        if code == "unprocessable":
            message_key = _unprocessable_message_key(err.details)
        elif code == "tunnels_disabled":
            message_key = _tunnels_disabled_message_key(err.details)
        return ErrorView(
            code=code,
            title_key=title_key,
            message_key=message_key,
            retryable=code in _RETRY_CODES,
            extra_actions=_extra_actions_for(code),
        )
    if isinstance(err, EngineUnavailable):
        if not err.retryable:
            return ErrorView(
                "unauthorized",
                "err.unauthorized.title",
                "err.domain.engine_unavailable_config",
                retryable=False,
            )
        return ErrorView(
            "unavailable",
            "err.unavailable.title",
            "err.domain.engine_unavailable",
            retryable=True,
        )
    if isinstance(err, (NotOwned, WorkspaceMissing)):
        return ErrorView("not_found", "err.not_found.title", "err.not_found", retryable=False)
    if isinstance(err, InvalidInput):
        key = "err.domain.invalid_input"
        if err.field == "package":
            key = (
                "err.domain.invalid_input.package_empty"
                if err.reason == "empty"
                else "err.domain.invalid_input.package_invalid"
            )
        elif err.field == "port":
            key = {
                "empty": "err.domain.invalid_input.port_empty",
                "out_of_range": "err.domain.invalid_input.port_out_of_range",
            }.get(err.reason, "err.domain.invalid_input.port_invalid")
        elif err.field == "interval":
            key = {
                "below_min": "err.domain.invalid_input.interval_below_min",
                "above_max": "err.domain.invalid_input.interval_above_max",
            }.get(err.reason, "err.domain.invalid_input.interval_invalid")
        elif err.field == "process":
            key = "err.domain.invalid_input.process_invalid"
        elif err.field == "archive":
            key = {
                "empty": "err.domain.invalid_input.archive_empty",
                "too_large": "err.domain.invalid_input.archive_too_large",
            }.get(err.reason, "err.domain.invalid_input")
        elif err.reason == "too_long":
            key = "err.domain.invalid_input.name_too_long"
        elif err.reason == "empty":
            key = "err.domain.invalid_input.name_empty"
        elif err.reason == "above_max":
            key = "err.domain.invalid_input.above_max"
        elif err.reason == "too_large":
            key = "err.domain.invalid_input.too_large"
        return ErrorView("invalid_input", "err.title.couldnt", key, retryable=True)
    if isinstance(err, RateLimited):
        return ErrorView(
            "rate_limited", "err.title", "err.domain.rate_limited", retryable=False
        )
    log.warning("error.unmapped", kind=type(err).__name__)
    return ErrorView("unknown", "err.unknown.title", "err.unknown", retryable=False)


def error_screen(
    err: DomainError,
    locale: Locale,
    *,
    mode: RunMode = RunMode.PRODUCTION,
    is_admin: bool = False,
    raw_payload: str | None = None,
    back_action: str = "nav:menu",
) -> Screen:
    view = view_for_error(err)
    doc = RichDoc(locale)
    doc.h(2, catalog.t(view.title_key, locale))
    doc.p(catalog.t(view.message_key, locale))
    if view.code == "workspace_suspended":
        doc.p(catalog.t("err.workspace_suspended.hint", locale))
    elif view.retryable:
        doc.p(catalog.t("err.hint", locale))

    rows: list[list] = []
    extra = [inline(catalog.t(k, locale), action, intent=intent) for k, action, intent in view.extra_actions]
    if view.retryable:
        extra.append(inline(catalog.t("common.retry", locale), "nav:refresh", intent=Intent.PRIMARY))
    if extra:
        rows.append(extra)
    rows.append([inline(catalog.t("common.back", locale), back_action, intent=Intent.PRIMARY)])

    if (
        raw_payload
        and mode is RunMode.DEBUG
        and is_admin
        and shows_raw_payloads(mode)
    ):
        RawPayloadDetails(raw_payload, mode=mode, is_admin=is_admin).render(doc)

    return Screen(doc=doc, markup=inline_markup(rows))


def error_code_screen(code: str, locale: Locale, **kwargs) -> Screen:
    """Render an error screen directly from a code (for tests and internal use)."""
    known = code if code in ERROR_KEYS else "unknown"
    if code not in ERROR_KEYS:
        log.warning("error.unknown_code", code=code)
    title_key, message_key = ERROR_KEYS[known]
    doc = RichDoc(locale)
    doc.h(2, catalog.t(title_key, locale))
    doc.p(catalog.t(message_key, locale))
    rows = [[inline(catalog.t("common.back", locale), "nav:menu", intent=Intent.PRIMARY)]]
    return Screen(doc=doc, markup=inline_markup(rows))
