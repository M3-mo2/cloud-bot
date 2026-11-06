"""Screen-analysis helpers, shared by the doctor and the screen test suite.

These are pure functions over a rendered ``Screen`` (and over the catalog's
builders). The contract test (``screens-b``), the pager audit (``test_pagination``)
and the label guard (``test_menu``) use them, so there is exactly **one**
implementation of each rule — the doctor and the tests cannot drift.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from cloudbot.presentation import pagination

#: Screens whose rows are a closed, compile-time set (an enum), so they cannot
#: grow with the data and legitimately need no pager. Shared with
#: ``tests/unit/test_pagination.py`` so the rule has one source.
FIXED_ROW_SCREENS: frozenset[str] = frozenset({"admin_notifications"})

#: Transient cards that are *sent*, not navigated: a notification message or a
#: progress notice has no way out by design, so "no exit" is not a defect.
TRANSIENT_SCREENS: frozenset[str] = frozenset(
    {"admin_notify_message", "admin_notify_gap", "admin_notify_capped", "tunnel_publishing"}
)

#: Callbacks that land on a paged list; a screen carrying one has a pager.
PAGER_ACTIONS: frozenset[str] = frozenset(
    {
        "ws:list",
        "term:list",
        "file:ls",
        "port:list",
        "proc:list",
        "pkg:list",
        "met:view",
        "tnl:list",
        "arc:result",
        "run:history",
    }
)

def rows(screen) -> list[list]:
    markup = screen.markup
    if markup is None:
        return []
    return list(getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or [])

def buttons(screen) -> list:
    return [button for row in rows(screen) for button in row]

def callbacks(screen) -> list[str]:
    return [b.callback_data for b in buttons(screen) if getattr(b, "callback_data", None)]

def table_columns(html: str) -> int:
    """The widest table row, counted exactly as it renders (headers included)."""
    widest = 0
    for table in html.split("<table")[1:]:
        head = table.split("</table>")[0]
        for tr in head.split("<tr>")[1:]:
            tr = tr.split("</tr>")[0]
            widest = max(widest, tr.count("<th") + tr.count("<td"))
    return widest

def table_rows(html: str) -> int:
    """Data rows in *list-shaped* tables only (a ``<th>``+``<td>`` fact table is not a list)."""
    total = 0
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.DOTALL):
        table_rows_ = re.findall(r"<tr>(.*?)</tr>", table, flags=re.DOTALL)
        if not table_rows_:
            continue
        header = table_rows_[0]
        body = table_rows_[1:]
        if "<td>" in header or not body:
            continue
        if all("<td>" in row and "<th>" not in row for row in body):
            total += len(body)
    return total

def has_pager(screen) -> bool:
    return any(
        action in PAGER_ACTIONS
        or action.startswith(tuple(a + ":" for a in PAGER_ACTIONS))
        for action in callbacks(screen)
    )

def duplicate_destinations(screen) -> list[str]:
    """Actions named more than once on one screen (a wasted slot)."""
    return [action for action, count in Counter(callbacks(screen)).items() if count > 1]

def leading_emoji(text: str) -> str:
    """The leading emoji/flag of a button label (the whole first token)."""
    return text.split(" ", 1)[0]

def emoji_duplicates_on_screen(screen) -> list[tuple[str, list[str]]]:
    by_emoji: dict[str, list[str]] = defaultdict(list)
    for button in buttons(screen):
        by_emoji[leading_emoji(button.text)].append(button.text)
    return [
        (emoji, labels)
        for emoji, labels in by_emoji.items()
        if len(labels) > 1 and emoji
    ]

def wide_rows(screen, *, max_buttons: int = 2) -> list[int]:
    """Indices of rows carrying more than ``max_buttons`` buttons."""
    return [index for index, row in enumerate(rows(screen)) if len(row) > max_buttons]

def nav_block_rows(screen) -> int:
    """Rows in the trailing nav block (contiguous trailing rows of nav actions)."""
    from cloudbot.presentation.screens import nav

    block = 0
    for row in reversed(rows(screen)):
        actions = {(getattr(b, "callback_data", None) or "") for b in row}
        if actions and actions <= set(nav.NAV_ACTIONS):
            block += 1
        else:
            break
    return block

def static_labels(locale) -> set[str]:
    """Catalogue values that are exactly a message with no substitution field."""
    from cloudbot.presentation.i18n.catalog import CATALOGS

    return {
        text
        for text in CATALOGS[locale].MESSAGES.values()
        if "{" not in text and "}" not in text
    }

def label_conflicts(names: Iterable[str], locale) -> dict[str, list[str]]:
    """Same destination (callback) wearing two *naming* labels across the catalog.

    Affordance labels (``nav.NAV_LABEL_EXCEPTIONS``) are removed: ``common.back``
    deliberately means "step back" on many screens while reaching one callback.
    Only static labels are compared (a label built from data is not a second name).
    """
    from cloudbot.presentation import catalog as screen_catalog
    from cloudbot.presentation.i18n.catalog import catalog
    from cloudbot.presentation.screens import nav

    static = static_labels(locale)
    exceptions = {catalog.t(key, locale) for key in nav.NAV_LABEL_EXCEPTIONS}
    shared = frozenset(nav.NAV_ACTIONS) | {"ws:new"}
    labels: dict[str, set[str]] = defaultdict(set)
    for name in names:
        screen = screen_catalog.build(name, locale)
        for button in buttons(screen):
            action = getattr(button, "callback_data", None)
            if action in shared and button.text in static and button.text not in exceptions:
                labels[action].add(button.text)
    return {action: sorted(texts) for action, texts in labels.items() if len(texts) > 1}

def no_exit(screen) -> bool:
    """A screen with no button at all has no way out."""
    return not buttons(screen)

def oversized_without_pager(screen) -> int:
    """The list-row count when it exceeds a page with no pager, else 0."""
    count = table_rows(screen.doc.html)
    if count > pagination.PAGE_SIZE and not has_pager(screen):
        return count
    return 0

__all__ = [
    "FIXED_ROW_SCREENS",
    "PAGER_ACTIONS",
    "TRANSIENT_SCREENS",
    "buttons",
    "callbacks",
    "duplicate_destinations",
    "emoji_duplicates_on_screen",
    "has_pager",
    "label_conflicts",
    "leading_emoji",
    "nav_block_rows",
    "no_exit",
    "oversized_without_pager",
    "rows",
    "static_labels",
    "table_columns",
    "table_rows",
    "wide_rows",
]
