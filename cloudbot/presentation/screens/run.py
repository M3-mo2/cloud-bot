"""Execution screens: run-mode chooser, exec prompt/result, job start, and live logs."""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.entities import Run, Workspace
from cloudbot.domain.value_objects import ExecResult, RunStatus, WorkspaceId
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import (
    format_bytes,
    format_duration,
    format_ms,
    format_seconds,
    localize_digits,
)
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.mapping.statuses import run_status_heading, run_status_label
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, italic
from cloudbot.presentation.screens.screen import Screen


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _raw(text: str) -> Html:
    return Html(text)


def _th(label: str) -> Html:
    return _raw(f"<th>{label}</th>")


# -- run mode chooser --------------------------------------------------------
def run_mode(ws: Workspace, locale: Locale, *, has_files: bool = False) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.title", locale))
    doc.p(_raw(catalog.t("run.how", locale, name=_esc(ws.name))))
    doc.ul(
        [
            _raw(catalog.t("run.opt.quick", locale)),
            _raw(catalog.t("run.opt.background", locale)),
        ]
    )
    rows = [
        [
            inline(catalog.t("run.quick", locale), _cb("exec:prompt", ws.id), intent=Intent.PRIMARY),
            inline(catalog.t("run.background", locale), _cb("run:prompt", ws.id), intent=Intent.SUCCESS),
        ]
    ]
    if has_files:
        rows.append([inline(catalog.t("run.run_file", locale), _cb("run:pick_file", ws.id, 0), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("common.cancel", locale), _cb("ws:view", ws.id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


# -- exec --------------------------------------------------------------------
def exec_prompt(ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("exec.quick_title", locale))
    doc.p(_raw(catalog.t("exec.prompt", locale)))
    doc.p(italic(catalog.t("exec.prompt_hint", locale)))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def exec_result(
    result: ExecResult,
    ws_id: WorkspaceId,
    locale: Locale,
    *,
    missing_offer: tuple[str, list[list]] | None = None,
    again_action: str | None = None,
) -> Screen:
    """A finished command's result.

    ``missing_offer`` is the package screen's ``(html, rows)`` offer for the
    modules the traceback named; when present it sits **above** the result facts,
    exactly where the failure is read. ``again_action`` re-runs the exact command
    that failed — offered as a button, never run automatically.
    """
    doc = RichDoc(locale)

    offer_html, offer_rows = missing_offer if missing_offer is not None else ("", [])

    if result.exit_code == 124:
        doc.h(2, catalog.t("exec.timed_out", locale))
    elif result.exit_code == 0:
        doc.h(2, catalog.t("exec.finished", locale, code=_num(0, locale)))
    else:
        doc.h(2, catalog.t("exec.failed", locale, code=_num(result.exit_code, locale)))

    if offer_html:
        # Directly under the failure it explains, and above the result facts.
        doc.raw(offer_html)

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if stdout:
        doc.pre(stdout, language="text")
    elif not stderr:
        doc.p(catalog.t("exec.no_output", locale))

    stderr_summary = catalog.t("exec.stderr", locale) if stderr else catalog.t("exec.stderr_empty", locale)
    doc.raw(
        f"<details{' open' if stderr else ''}><summary>{_esc(stderr_summary)}</summary>"
        f"<pre><code class=\"language-text\">{_esc(stderr)}</code></pre></details>"
    )
    if result.truncated:
        doc.p(catalog.t("exec.truncated", locale))
    doc.p(italic(format_ms(result.duration_ms, locale)))

    rows: list[list] = []
    if again_action is not None:
        # Beside the result it repeats: the same command, on one tap.
        rows.append(
            [inline(catalog.t("exec.again_same", locale), again_action, intent=Intent.SUCCESS)]
        )
    rows += offer_rows
    rows.append(
        [
            inline(catalog.t("exec.run_again", locale), _cb("exec:prompt", ws_id), intent=Intent.PRIMARY),
            inline(catalog.t("run.background", locale), _cb("run:prompt", ws_id), intent=Intent.SUCCESS),
        ]
    )
    rows.append(
        [
            inline(catalog.t("common.clear", locale), "nav:clear", intent=Intent.PRIMARY),
            inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
        ]
    )
    return Screen(doc=doc, markup=inline_markup(rows))

# -- background job ----------------------------------------------------------
def file_picker(entries, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.run_file", locale))
    doc.p(catalog.t("run.job_hint", locale))
    rows = []
    for entry in entries:
        rows.append([inline(f"📄 {entry.name}", _cb("run:file", ws_id, entry.path), intent=Intent.PRIMARY)])
    rows.append([inline(catalog.t("common.cancel", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


def job_prompt(ws_id: WorkspaceId, locale: Locale, *, file_path: str | None = None) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.job_title", locale))
    if file_path:
        doc.p(_raw(f"<code>{_esc(file_path)}</code>"))
    doc.p(catalog.t("run.job_prompt", locale))
    doc.p(italic(catalog.t("run.job_hint", locale)))
    if file_path:
        doc.p(catalog.t("run.args_prompt", locale))
    markup = inline_markup(
        [[inline(catalog.t("common.cancel", locale), "nav:cancel", intent=Intent.PRIMARY)]],
        force_reply=True,
    )
    return Screen(doc=doc, markup=markup, send_new=True)


def run_started(run: Run, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.started", locale))
    cmd = " ".join(run.argv) if run.argv else ""
    short_id = _short_id(str(run.id))
    doc.table(
        None,
        [
            [_th(catalog.t("run.column.status", locale)), run_status_label(run.status, locale)],
            [_th(catalog.t("run.column.command", locale)), _raw(f"<code>{_esc(cmd)}</code>")],
            [_th(catalog.t("run.column.cwd", locale)), _raw(f"<code>{_esc(run.cwd or '/')}</code>")],
            [_th(catalog.t("run.column.id", locale)), _raw(f"<code>{_esc(short_id)}</code>")],
        ],
    )
    doc.p(italic(catalog.t("run.started_hint", locale)))
    markup = run_started_markup(run, locale)
    return Screen(doc=doc, markup=markup)


def run_started_markup(run: Run, locale: Locale):
    ws_id = run.workspace_id
    return inline_markup(
        [
            [
                inline(catalog.t("run.live_logs", locale), _cb("run:logs", ws_id, run.id), intent=Intent.PRIMARY),
                inline(catalog.t("common.refresh", locale), _cb("run:refresh", ws_id, run.id), intent=Intent.PRIMARY),
            ],
            [
                inline(catalog.t("run.stop", locale), _cb("run:stop_confirm", ws_id, run.id), intent=Intent.DANGER),
                inline(catalog.t("run.history", locale), _cb("run:history", ws_id), intent=Intent.PRIMARY),
            ],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )


# -- live logs ---------------------------------------------------------------
def live_logs(
    run: Run,
    locale: Locale,
    *,
    stdout: str,
    stderr: str = "",
    truncated: bool = False,
    elapsed_s: float | None = None,
    bounded_kb: int | None = None,
) -> Screen:
    from cloudbot.services.files import tail_preview

    doc = RichDoc(locale)
    if run.status.is_terminal:
        doc.h(2, run_status_heading(run.status, locale, exit_code=run.exit_code))
    else:
        doc.h(2, catalog.t("run.live_logs", locale))
        doc.p(_raw(f"<b>{catalog.t('run.tail_status', locale)}</b> {run_status_label(run.status, locale)}"))

    shown, trimmed = tail_preview(stdout or "")
    if shown:
        doc.pre(shown, language="text")

    stderr_key = catalog.t("exec.stderr", locale) if stderr else catalog.t("exec.stderr_empty", locale)
    doc.raw(
        f"<details{' open' if stderr else ''}><summary>{_esc(stderr_key)}</summary>"
        f"<pre><code class=\"language-text\">{_esc(stderr or '')}</code></pre></details>"
    )

    # A run started from a file fails for the same reasons a typed command does,
    # and this screen is where that failure is read. The offer is built from the
    # run's own stderr, so a missing package is one tap away here as well.
    from cloudbot.presentation.screens.packages import missing_section

    offer_html, offer_rows = missing_section(stderr or "", run.workspace_id, locale)
    if offer_html:
        doc.raw(offer_html)

    if truncated:
        doc.blockquote(catalog.t("run.log_truncated", locale))
    if trimmed:
        doc.p(italic(catalog.t("run.output_bounded", locale, kb=_num(bounded_kb or 12, locale))))

    if run.status.is_terminal:
        # The command is here because the history row no longer carries it; this
        # is the run's own screen and the one place its full identity lives.
        facts = [
            (catalog.t("run.column.command", locale), _raw(f"<code>{_esc(run.command_display)}</code>")),
        ]
        if run.exit_code is not None:
            facts.append(
                (catalog.t("run.exit_code", locale), _raw(f"<code>{_num(run.exit_code, locale)}</code>"))
            )
        facts.append((catalog.t("run.duration", locale), _run_duration(run, elapsed_s, locale)))
        facts.append(
            (catalog.t("run.output", locale), format_bytes(run.stdout_bytes + run.stderr_bytes, locale))
        )
        doc.kv_table(facts)
    else:
        doc.p(_raw(f"<b>{catalog.t('run.column.command', locale)}:</b> "
                   f"<code>{_esc(run.command_display)}</code>"))
        doc.p(italic(_progress_line(run, elapsed_s, locale)))

    markup = live_logs_markup(run, locale, extra_rows=offer_rows)
    return Screen(doc=doc, markup=markup)


def _progress_line(run: Run, elapsed_s: float | None, locale: Locale) -> str:
    from cloudbot.presentation.i18n.formatters import format_duration

    if elapsed_s is None:
        elapsed_s = 0.0
    if run.unlimited or run.timeout_sec is None:
        return catalog.t("run.running_elapsed", locale, elapsed=format_duration(elapsed_s, locale))
    pct = min(100.0, elapsed_s / max(1, run.timeout_sec) * 100)
    return catalog.t(
        "run.progress",
        locale,
        pct=_num(int(pct), locale),
        elapsed=format_duration(elapsed_s, locale),
        limit=format_duration(run.timeout_sec, locale),
    )


def _run_duration(run: Run, elapsed_s: float | None, locale: Locale) -> str:
    if elapsed_s is not None:
        return format_seconds(elapsed_s, locale)
    if run.started_at and run.ended_at:
        return format_seconds((run.ended_at - run.started_at).total_seconds(), locale)
    return "—"


def live_logs_markup(run: Run, locale: Locale, *, extra_rows: list[list] | None = None):
    """The live log screen's buttons.

    ``extra_rows`` carries the missing-package offer built from the run's stderr;
    it sits before the run controls so the fix is the first thing offered.
    """
    ws_id = run.workspace_id
    head = list(extra_rows or [])
    if run.status.is_terminal:
        rows = [
            [
                inline(catalog.t("exec.run_again", locale), _cb("run:file", ws_id, str(run.id)), intent=Intent.PRIMARY),
                inline(catalog.t("run.delete_log", locale), _cb("run:delete", ws_id, run.id), intent=Intent.DANGER),
            ],
            [
                inline(catalog.t("common.clear", locale), "nav:clear", intent=Intent.PRIMARY),
                inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY),
            ],
        ]
        return inline_markup([*head, *rows])
    # No "clear output" here: the engine has no log-truncation endpoint, so the
    # old button just re-read the same bytes and was indistinguishable from
    # Refresh (it even carried the same callback). Two destinations exist while a
    # run is live — Stop and Refresh — and the output clears when the run does.
    return inline_markup(
        [
            *head,
            [
                inline(catalog.t("run.stop", locale), _cb("run:stop_confirm", ws_id, run.id), intent=Intent.DANGER),
                inline(catalog.t("common.refresh", locale), _cb("run:refresh", ws_id, run.id), intent=Intent.PRIMARY),
            ],
            [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
        ]
    )


# -- stop confirm ------------------------------------------------------------
def stop_confirm(run: Run, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    doc.h(2, catalog.t("run.stop_title", locale))
    doc.p(catalog.t("run.stop_body", locale))
    markup = inline_markup(
        [
            [
                inline(catalog.t("run.stop_confirm", locale), _cb("run:stop", run.workspace_id, run.id), intent=Intent.DANGER),
                inline(catalog.t("common.cancel", locale), _cb("run:logs", run.workspace_id, run.id), intent=Intent.PRIMARY),
            ]
        ]
    )
    return Screen(doc=doc, markup=markup)


def _short_id(run_id: str) -> str:
    if len(run_id) <= 14:
        return run_id
    return run_id[:12] + "…"


import base64 as _base64


def _b64(text: str) -> str:
    return _base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _esc(text: str) -> str:
    from cloudbot.presentation.rich.tags import esc

    return esc(text)
