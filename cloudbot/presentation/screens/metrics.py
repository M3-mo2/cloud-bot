"""Metrics screen (Phase 2d): one-shot RAM/disk snapshot with honest completeness.

The panel is a **snapshot the user refreshes** (see ``services/metrics.py`` for
why there is no standing SSE connection). ``ram_complete``/``disk_complete`` are
honesty flags: a figure whose flag is false is a lower bound and is rendered as a
provisional value with **no percentage and no bar** — never as a confident zero,
and never as a bar drawn from a lower bound. A workspace with no live cgroup
(``live_cgroups: 0`` and RAM 0) is "nothing running", not an error, and is shown
as one explicit idle line.

The page answers two different questions, so it is two groups: **capacity**
(memory and disk — "am I full?") and **activity** (live runs, control groups and
the write-rate ceiling — "is anything running?"). Capacity is a bold label, a
value and a proportional 20-cell bar; there is no table and no pager, because
four fixed figures do not grow and a percentage in a table cell does not answer
"how full is it" at a glance.

The body is built in **blocks**, not html, because the refresh button acts on the
snapshot being displayed and therefore sits inside the message body
(``doc.buttons``); ``InputRichMessage`` takes either ``html`` or ``blocks``, never
both (see ``admin/user_page.py``). The keyboard below keeps only the two
destinations that leave the snapshot: Processes and Back.
"""

from __future__ import annotations

from aiogram.types import RichTextBold, RichTextItalic, RichTextMarked

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceMetrics
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_bytes, localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.callbacks import cb as _cb
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.screen import Screen

#: The bar is 20 cells: enough resolution to see "nearly full" without wrapping
#: on a phone. The filled/empty pair are both block elements, so Telegram's font
#: gives them the same advance width and the bar's edges line up in every client.
BAR_CELLS = 20
BAR_FILLED = "█"
BAR_EMPTY = "░"

#: The tone thresholds live in the catalog (see ``metrics.threshold.*``): the
#: cut-off and the words that explain it move together, not apart.
_WARNING_KEY = "metrics.threshold.warning"
_DANGER_KEY = "metrics.threshold.danger"

_PLAIN = "plain"
_WARNING = "warning"
_DANGER = "danger"


def _num(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


def _threshold(key: str, locale: Locale) -> float:
    return float(catalog.t(key, locale))


def bar(percent: float) -> str:
    """A proportional 20-cell bar; an out-of-range reading is clamped, never wrapped."""
    filled = round(percent / 100 * BAR_CELLS)
    filled = max(0, min(BAR_CELLS, filled))
    return BAR_FILLED * filled + BAR_EMPTY * (BAR_CELLS - filled)


def tone(percent: float, locale: Locale) -> str:
    """``plain`` / ``warning`` / ``danger`` from the catalogued thresholds."""
    if percent >= _threshold(_DANGER_KEY, locale):
        return _DANGER
    if percent >= _threshold(_WARNING_KEY, locale):
        return _WARNING
    return _PLAIN


def _tone_marker(tone_name: str, locale: Locale) -> str:
    if tone_name == _DANGER:
        return catalog.t("metrics.tone.danger", locale) + " "
    if tone_name == _WARNING:
        return catalog.t("metrics.tone.warning", locale) + " "
    return ""


def _percent_span(percent: float, tone_name: str, locale: Locale):
    """The percentage, bold; marked (highlighted) when headroom is short.

    Rich blocks have no text colour, so ``RichTextMarked`` is the only emphasis
    Telegram exposes for a value's tone; the marker emoji in front carries the
    same meaning for a client that renders marked text plainly.
    """
    text = catalog.t(
        "metrics.percent", locale, p=localize_digits(str(round(percent)), locale)
    )
    bold = RichTextBold(text=text)
    if tone_name in (_WARNING, _DANGER):
        return RichTextMarked(text=bold)
    return bold


def _capacity_line(
    label_key: str,
    used: int,
    limit: int,
    percent: float,
    complete: bool,
    locale: Locale,
):
    """One capacity figure: ``**label** ⟸ used of limit · **pct**``.

    An incomplete read is a lower bound: the value is shown with the provisional
    marker and **no** percentage. A bar from that value is a confident lie in
    graph form, so the caller must not draw one.
    """
    used_text = format_bytes(used, locale)
    label = RichTextBold(text=catalog.t(label_key, locale))
    if not complete:
        return [
            label,
            " ⟸ ",
            used_text,
            " ",
            RichTextItalic(
                text=f"({catalog.t('metrics.incomplete', locale)})"
            ),
        ]
    limit_text = format_bytes(limit, locale)
    tone_name = tone(percent, locale)
    parts = [
        label,
        " ⟸ ",
        used_text,
        f" {catalog.t('metrics.of', locale)} ",
        limit_text,
        " · ",
    ]
    marker = _tone_marker(tone_name, locale)
    if marker:
        parts.append(marker)
    parts.append(_percent_span(percent, tone_name, locale))
    return parts


def metrics_screen(metrics: WorkspaceMetrics, ws_id: WorkspaceId, locale: Locale) -> Screen:
    doc = RichDoc(locale)
    rule = catalog.t("ws.head.rule", locale)
    doc.paragraph(rule)
    doc.heading(
        f"✦ {catalog.t('metrics.subject', locale)} ⟸ "
        f"{catalog.t('metrics.header_value', locale)}",
        size=2,
    )
    doc.paragraph(rule)

    capacity = (
        (
            "metrics.ram",
            metrics.ram_used_bytes,
            metrics.ram_limit_bytes,
            metrics.ram_percent,
            metrics.ram_complete,
        ),
        (
            "metrics.disk",
            metrics.disk_used_bytes,
            metrics.disk_limit_bytes,
            metrics.disk_percent,
            metrics.disk_complete,
        ),
    )
    any_incomplete = not metrics.ram_complete or not metrics.disk_complete
    any_danger = False
    for label_key, used, limit, percent, complete in capacity:
        doc.paragraph_rich(
            _capacity_line(label_key, used, limit, percent, complete, locale)
        )
        if complete:
            doc.paragraph(bar(percent))
            any_danger = any_danger or tone(percent, locale) == _DANGER

    # The provisional note is said **once** for the group, not once per row.
    if any_incomplete:
        doc.paragraph(catalog.t("metrics.incomplete_note", locale))
    # The advice is said once too: it is the same remedy for either figure.
    if any_danger:
        doc.paragraph(catalog.t("metrics.advice.full", locale))

    doc.divider()

    # Activity: "is anything running?" — one line when nothing is, not a
    # paragraph plus an italic hint saying the same thing twice.
    if metrics.live_cgroups == 0:
        doc.paragraph(catalog.t("metrics.idle", locale))
    else:
        doc.paragraph(
            f"{catalog.t('metrics.live_runs', locale)} ⟸ "
            f"{_num(metrics.live_runs, locale)}"
        )
        doc.paragraph(
            f"{catalog.t('metrics.live_cgroups', locale)} ⟸ "
            f"{_num(metrics.live_cgroups, locale)}"
        )

    if metrics.write_rate_limit_mb_s > 0:
        write_value = catalog.t(
            "metrics.write_rate.value",
            locale,
            m=_num(metrics.write_rate_limit_mb_s, locale),
        )
    else:
        write_value = catalog.t("metrics.write_rate.none", locale)
    doc.paragraph(f"{catalog.t('metrics.write_rate', locale)} ⟸ {write_value}")

    # The refresh acts on the snapshot above it, so it sits beside the numbers.
    doc.buttons(
        [
            (
                catalog.t("metrics.refresh", locale),
                _cb("met:view", ws_id),
                "primary",
            )
        ]
    )

    doc.paragraph(
        catalog.t(
            "metrics.sampled",
            locale,
            n=_num(metrics.interval_seconds, locale),
        )
    )

    return Screen(doc=doc, markup=metrics_markup(ws_id, locale))


def metrics_markup(ws_id: WorkspaceId, locale: Locale):
    rows = [
        [inline(catalog.t("ws.processes", locale), _cb("proc:list", ws_id), intent=Intent.PRIMARY)],
        [inline(catalog.t("common.back", locale), _cb("ws:view", ws_id), intent=Intent.PRIMARY)],
    ]
    return inline_markup(rows)
