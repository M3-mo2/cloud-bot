"""TASK metrics-shape: the resources page reads as a bar chart, not a table.

What this file defends, and what fails on the pre-task screen:

* a capacity figure is **one block**: a bold label, the value and a real 20-cell
  bar whose fill matches the percentage (the old screen put the percentage in a
  bordered table cell and drew no bar at all);
* the page carries **no table and no pager** — four fixed rows never paginate,
  and ``صفحة 1 من 1`` was pure noise;
* the honesty flags still win: an incomplete read has no percentage **and no
  bar**, because a bar from a lower bound is a confident lie in graph form;
* the value's tone is warning at 75% and danger at 90% (with one line of advice),
  plain below that;
* idle says one thing once;
* the write-rate ceiling is shown, and «بلا حد» when it is zero.

The body is built in **blocks** (the refresh button sits beside the snapshot it
acts on), so the "document" here is the built ``InputRichMessage`` serialised as
JSON — that is what the golden suite locks and what a client receives.
"""

from __future__ import annotations

import json

import pytest
from aiogram.types import RichMessageButton

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceMetrics
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.screens import metrics as met_screens

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
LOCALES = (Locale.EN, Locale.AR)


def _metrics(
    *,
    ram_used: int = 412 * 1024 * 1024,
    ram_limit: int = 1024 * 1024 * 1024,
    ram_percent: float = 40.0,
    ram_complete: bool = True,
    disk_used: int = int(1.2 * 1024**3),
    disk_limit: int = 5 * 1024**3,
    disk_percent: float = 24.0,
    disk_complete: bool = True,
    write_rate: int = 0,
    live_runs: int = 2,
    live_cgroups: int = 2,
) -> WorkspaceMetrics:
    return WorkspaceMetrics(
        workspace_id=str(WS),
        timestamp="2026-11-10T08:00:00Z",
        interval_seconds=5,
        ram_used_bytes=ram_used,
        ram_limit_bytes=ram_limit,
        ram_percent=ram_percent,
        ram_complete=ram_complete,
        disk_used_bytes=disk_used,
        disk_limit_bytes=disk_limit,
        disk_percent=disk_percent,
        disk_complete=disk_complete,
        write_rate_limit_mb_s=write_rate,
        live_runs=live_runs,
        live_cgroups=live_cgroups,
    )


def _document(metrics: WorkspaceMetrics, locale: Locale = Locale.EN) -> str:
    """The built message as JSON — the blocks form the user actually receives."""
    return met_screens.metrics_screen(metrics, WS, locale).doc.build().model_dump_json(
        exclude_none=True
    )


def _blocks(metrics: WorkspaceMetrics, locale: Locale = Locale.EN) -> list[dict]:
    return json.loads(_document(metrics, locale))["blocks"]


def _plain_text(blocks: list[dict]) -> str:
    """All the paragraph/heading text flattened, rich spans included.

    A block's spans are concatenated with no separator (that is how they render);
    blocks are joined with a space so a phrase cannot straddle two of them.
    """
    rendered: list[str] = []

    def walk(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(walk(item) for item in value)
        if isinstance(value, dict):
            return walk(value.get("text"))
        return ""

    for block in blocks:
        if block["type"] in {"paragraph", "heading", "footer"}:
            rendered.append(walk(block.get("text")))
    return " ".join(rendered)


def _bars(blocks: list[dict]) -> list[str]:
    cells = set(met_screens.BAR_FILLED + met_screens.BAR_EMPTY)
    return [
        block["text"]
        for block in blocks
        if block["type"] == "paragraph"
        and isinstance(block.get("text"), str)
        and len(block["text"]) == met_screens.BAR_CELLS
        and set(block["text"]) <= cells
    ]


def _buttons(screen) -> list:
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or []
    return [button for row in rows for button in row]


# -- the bite ----------------------------------------------------------------
def test_a_complete_figure_draws_a_twenty_cell_bar_that_matches_the_percentage():
    """The whole point of the screen: a bar that answers "how full is it"."""
    metrics = _metrics(ram_percent=40.0, disk_percent=24.0)
    blocks = _blocks(metrics)
    bars = _bars(blocks)
    assert len(bars) == 2, f"expected one bar per capacity figure, got {bars}"

    for raw, percent in zip(bars, (40.0, 24.0)):
        assert len(raw) == met_screens.BAR_CELLS == 20
        filled = raw.count(met_screens.BAR_FILLED)
        expected = round(percent / 100 * met_screens.BAR_CELLS)
        assert abs(filled - expected) <= 1, (raw, percent)

    # The old screen's bordered table is gone, in both forms of the document.
    document = _document(metrics)
    assert "<table" not in document
    assert "<table" not in met_screens.metrics_screen(metrics, WS, Locale.EN).doc.html


def test_the_value_line_is_label_then_value_with_no_table_cell():
    text = _plain_text(_blocks(_metrics()))
    assert "Memory ⟸ 412 MB of 1.0 GB · 40%" in text
    assert "Disk ⟸ 1.2 GB of 5.0 GB · 24%" in text


# -- honesty flags -----------------------------------------------------------
def test_incomplete_ram_has_no_bar_and_no_percentage():
    metrics = _metrics(ram_used=0, ram_percent=99.0, ram_complete=False)
    blocks = _blocks(metrics)
    # Only the disk bar survives; RAM has none.
    assert len(_bars(blocks)) == 1
    text = _plain_text(blocks)
    ram_line = next(
        block for block in blocks
        if block["type"] == "paragraph"
        and block.get("text")
        and "Memory" in json.dumps(block["text"], ensure_ascii=False)
    )
    ram_json = json.dumps(ram_line["text"], ensure_ascii=False)
    assert "%" not in ram_json, "an incomplete RAM figure must not carry a percentage"
    assert "provisional" in ram_json.lower()
    assert catalog.t("metrics.incomplete_note", Locale.EN) in text


def test_incomplete_disk_has_no_bar_and_no_percentage():
    metrics = _metrics(disk_percent=99.0, disk_complete=False)
    blocks = _blocks(metrics)
    assert len(_bars(blocks)) == 1
    assert "99%" not in _plain_text(blocks)
    assert "provisional" in _plain_text(blocks).lower()
    assert catalog.t("metrics.incomplete_note", Locale.EN) in _plain_text(blocks)


def test_incompleteness_is_explained_once_not_per_row():
    metrics = _metrics(ram_complete=False, disk_complete=False)
    text = _plain_text(_blocks(metrics))
    assert text.count(catalog.t("metrics.incomplete_note", Locale.EN)) == 1


# -- tone by headroom --------------------------------------------------------
def test_forty_percent_is_plain():
    text = _plain_text(_blocks(_metrics(ram_percent=40.0)))
    assert catalog.t("metrics.tone.warning", Locale.EN) not in text
    assert catalog.t("metrics.tone.danger", Locale.EN) not in text
    assert catalog.t("metrics.advice.full", Locale.EN) not in text


def test_seventy_five_percent_is_the_warning_tone():
    text = _plain_text(_blocks(_metrics(ram_percent=75.0)))
    assert catalog.t("metrics.tone.warning", Locale.EN) in text
    assert catalog.t("metrics.tone.danger", Locale.EN) not in text
    assert catalog.t("metrics.advice.full", Locale.EN) not in text


def test_ninety_two_percent_is_the_danger_tone_and_carries_advice():
    text = _plain_text(_blocks(_metrics(ram_percent=92.0)))
    assert catalog.t("metrics.tone.danger", Locale.EN) in text
    assert catalog.t("metrics.advice.full", Locale.EN) in text


def test_the_thresholds_come_from_the_catalog():
    """The cut-offs are data: the screen must not hardcode 75/90."""
    assert met_screens.tone(74.9, Locale.EN) == "plain"
    assert met_screens.tone(75.0, Locale.EN) == "warning"
    assert met_screens.tone(89.9, Locale.EN) == "warning"
    assert met_screens.tone(90.0, Locale.EN) == "danger"
    assert catalog.t("metrics.threshold.warning", Locale.EN) == "75"
    assert catalog.t("metrics.threshold.danger", Locale.EN) == "90"


# -- no pager ----------------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_screen_carries_no_page_footer_and_no_pager_rows(locale):
    metrics = _metrics()
    screen = met_screens.metrics_screen(metrics, WS, locale)
    document = screen.doc.build().model_dump_json(exclude_none=True)
    footer = catalog.t(
        "common.page", locale, page="1", total="1"
    )
    assert footer not in document
    assert "Page " not in document and "صفحة " not in document

    actions = [b.callback_data for b in _buttons(screen) if b.callback_data]
    assert not any(a.startswith("met:view") for a in actions), actions
    assert catalog.t("common.prev", locale) not in [b.text for b in _buttons(screen)]
    assert catalog.t("common.next", locale) not in [b.text for b in _buttons(screen)]


# -- idle says it once -------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_idle_produces_exactly_one_explanatory_line(locale):
    metrics = _metrics(ram_used=0, ram_percent=0.0, live_runs=0, live_cgroups=0)
    text = _plain_text(_blocks(metrics, locale))
    assert text.count(catalog.t("metrics.idle", locale)) == 1
    # The old second line ("memory reads 0 because…") is folded into the one line.
    assert not catalog.has("metrics.idle_hint", locale)


# -- the write-rate ceiling --------------------------------------------------
def test_a_non_zero_write_rate_is_shown():
    text = _plain_text(_blocks(_metrics(write_rate=12)))
    assert catalog.t("metrics.write_rate.value", Locale.EN, m="12") in text


@pytest.mark.parametrize("locale", LOCALES)
def test_a_zero_write_rate_says_no_limit(locale):
    text = _plain_text(_blocks(_metrics(write_rate=0), locale))
    assert catalog.t("metrics.write_rate.none", locale) in text


# -- buttons -----------------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_refresh_is_in_the_body_and_the_keyboard_keeps_processes_and_back(locale):
    screen = met_screens.metrics_screen(_metrics(), WS, locale)
    built = screen.doc.build()
    body_buttons = [
        button
        for block in (built.blocks or [])
        if type(block).__name__ == "InputRichBlockButtons"
        for button in block.buttons
    ]
    assert isinstance(body_buttons[0], RichMessageButton)
    assert body_buttons[0].text == catalog.t("metrics.refresh", locale)
    assert body_buttons[0].callback_data and body_buttons[0].callback_data.startswith("met:view")

    actions = [b.callback_data for b in _buttons(screen) if b.callback_data]
    assert actions == [
        f"proc:list:{WS}",
        f"ws:view:{WS}",
    ]
    for button in _buttons(screen) + body_buttons:
        assert len((button.callback_data or "").encode("utf-8")) <= 64
