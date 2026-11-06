"""The one pager every list screen uses.

Before this module each paginated screen hand-rolled its own slice, footer and
prev/next row, and the screens that did not bother grew without bound. A single
helper keeps the contract in one place: a page window, the ``Page n of m`` line,
and the previous/next row.

``PAGE_SIZE`` is one project-wide default. Ten rows is the number the workspace
list, the terminal list, the file browser and the run history already used; it
also keeps a page comfortably inside the chat viewport on a phone, which is the
only viewport this product has.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.tags import Html, italic

#: The project-wide page size (see the module docstring for why ten).
PAGE_SIZE = 10

T = TypeVar("T")
Element = str | Html


def total_pages_for(total: int, *, page_size: int = PAGE_SIZE) -> int:
    """How many pages ``total`` items need (at least one: an empty list is page 1/1)."""
    total = max(0, total)
    return max(1, (total + page_size - 1) // page_size)


def window(
    items: Sequence[T], page: int, *, page_size: int = PAGE_SIZE
) -> tuple[list[T], int, bool]:
    """Return ``(page_items, total_pages, has_more)`` for ``page``.

    An out-of-range page is clamped so a stale button (drawn before an item was
    removed) renders the nearest real page rather than an empty one.
    """
    total_pages = total_pages_for(len(items), page_size=page_size)
    page = max(0, min(int(page), total_pages - 1))
    start = page * page_size
    page_items = list(items[start : start + page_size])
    return page_items, total_pages, (start + page_size) < len(items)


def footer(
    page: int,
    total_pages: int,
    locale: Locale,
    *,
    key: str = "common.page",
    **params: object,
) -> Element:
    """The ``Page n of m`` line.

    ``key`` lets an existing screen keep its richer footer (the file browser
    prefixes the item count) while still going through this one helper; the
    default is the generic ``common.page`` key.
    """
    line = catalog.t(
        key,
        locale,
        page=localize_digits(str(page + 1), locale),
        total=localize_digits(str(max(1, total_pages)), locale),
        **params,
    )
    return italic(line)


def pager_rows(
    page: int,
    total_pages: int,
    action_for: Callable[[int], str],
    locale: Locale,
) -> list[list]:
    """The previous/next row, with an end's arrow omitted (not muted).

    This is the same R1 rule the nav block follows: a destination that does not
    exist yet is simply absent, never a disabled button that wastes a slot.
    """
    row = []
    if page > 0:
        row.append(
            inline(
                catalog.t("common.previous", locale),
                action_for(page - 1),
                intent=Intent.PRIMARY,
            )
        )
    if page + 1 < total_pages:
        row.append(
            inline(
                catalog.t("common.next", locale),
                action_for(page + 1),
                intent=Intent.PRIMARY,
            )
        )
    return [row] if row else []


__all__ = [
    "PAGE_SIZE",
    "footer",
    "pager_rows",
    "total_pages_for",
    "window",
]
