"""Run/workspace/session status → catalog key mapping (exhaustive)."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    RunStatus,
    SessionStatus,
    SummaryState,
    WorkspaceStatus,
)
from cloudbot.presentation.i18n.catalog import catalog

#: Emoji carried alongside each status copy (UI 05 §3–§5).
RUN_STATUS_EMOJI = {
    RunStatus.QUEUED: "⏳",
    RunStatus.RUNNING: "⏳",
    RunStatus.EXITED: "✅",
    RunStatus.KILLED: "⏹",
    RunStatus.TIMEOUT: "⏱️",
    RunStatus.FAILED: "⚠️",
    RunStatus.INTERRUPTED: "🔌",
    RunStatus.OOM: "🧠",
    RunStatus.RATE_LIMITED: "🐌",
    RunStatus.UNKNOWN: "⏳",
}

WORKSPACE_STATUS_EMOJI = {
    WorkspaceStatus.ACTIVE: "🟢",
    WorkspaceStatus.SUSPENDED: "🟠",
    WorkspaceStatus.LOCKED: "🔴",
    WorkspaceStatus.DELETED: "—",
    WorkspaceStatus.UNKNOWN: "—",
}

SUMMARY_STATE_EMOJI = {
    SummaryState.ACTIVE: "🟢",
    SummaryState.EXPIRING_SOON: "🟡",
    SummaryState.LOCKED: "🔴",
    SummaryState.UNLIMITED: "🔵",
    SummaryState.UNKNOWN: "—",
}

SESSION_STATUS_EMOJI = {
    SessionStatus.RUNNING: "🟢",
    SessionStatus.EXITED: "⚪",
    SessionStatus.KILLED: "⏹",
    SessionStatus.TIMED_OUT: "⏱️",
    SessionStatus.FAILED: "⚠️",
    SessionStatus.UNKNOWN: "—",
}


def run_status_key(status: RunStatus) -> str:
    return f"run.status.{status.value}"


def run_status_label(status: RunStatus, locale: Locale) -> str:
    return catalog.t(run_status_key(status), locale)


def run_status_heading(status: RunStatus, locale: Locale, *, exit_code: int | None = None) -> str:
    """The heading for a run status; ``exited`` distinguishes zero exit."""
    if status is RunStatus.EXITED and exit_code not in (None, 0):
        return catalog.t("run.status.exited.heading_nonzero", locale, code=_num(exit_code, locale))
    return catalog.t(f"{run_status_key(status)}.heading", locale)


def workspace_status_key(status: WorkspaceStatus) -> str:
    return f"ws.status.{status.value}"


def workspace_status_label(status: WorkspaceStatus, locale: Locale) -> str:
    return catalog.t(workspace_status_key(status), locale)


def summary_state_key(state: SummaryState) -> str:
    return f"summary.state.{state.value}"


def summary_state_label(state: SummaryState, locale: Locale) -> str:
    return catalog.t(summary_state_key(state), locale)


def session_status_key(status: SessionStatus) -> str:
    return f"session.status.{status.value}"


def session_status_label(status: SessionStatus, locale: Locale) -> str:
    return catalog.t(session_status_key(status), locale)


def _num(value: int, locale: Locale) -> str:
    from cloudbot.presentation.i18n.formatters import localize_digits

    return localize_digits(str(value), locale)


def display_state(status: WorkspaceStatus, state: SummaryState) -> tuple[str, str]:
    """Prefer ``summary.state`` when it is meaningful; fall back to ``status``.

    Returns ``(label_key, emoji)``.
    """
    if state in (SummaryState.EXPIRING_SOON, SummaryState.UNLIMITED):
        return summary_state_key(state), SUMMARY_STATE_EMOJI[state]
    if status is WorkspaceStatus.ACTIVE:
        return workspace_status_key(status), WORKSPACE_STATUS_EMOJI[status]
    return workspace_status_key(status), WORKSPACE_STATUS_EMOJI[status]
