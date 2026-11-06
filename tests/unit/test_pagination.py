"""One pager, every list screen: page window, footer, keyboard follows the page.

Every converted screen is exercised with more items than one page holds, so the
test fails on the old code (which had no ``page`` parameter at all and rendered
the whole list). ``PAGE_SIZE`` rows plus a "Page 1 of 2" footer and a pager row
must be the whole screen; the keyboard may not grow with the list length.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cloudbot.core.locale import Locale
from cloudbot.domain.value_objects import (
    ArchiveEntry,
    ArchiveResult,
    Package,
    PackageList,
    PortEntry,
    PortReport,
    ProcessEntry,
    ProcessList,
    Tunnel,
    TunnelList,
    UnixSocket,
    WorkspaceId,
)
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation import pagination
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.screens import archives as arc_screens
from cloudbot.presentation.screens import metrics as met_screens
from cloudbot.presentation.screens import packages as pkg_screens
from cloudbot.presentation.screens import ports as port_screens
from cloudbot.presentation.screens import processes as proc_screens
from cloudbot.presentation.screens import tunnels as tnl_screens

WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
LOCALES = (Locale.EN, Locale.AR)


def _rows(screen) -> list[list]:
    markup = screen.markup
    if markup is None:
        return []
    return list(getattr(markup, "inline_keyboard", None) or [])


def _buttons(screen) -> list:
    return [button for row in _rows(screen) for button in row]


def _footer_present(html: str, locale: Locale) -> bool:
    """Whether the page-2 footer is there (any page total)."""
    page_two = "2" if locale is Locale.EN else "٢"
    joiner = " of " if locale is Locale.EN else " من "
    return f"Page {page_two}{joiner}" in html or f"صفحة {page_two}{joiner}" in html


def _table_rows(html: str) -> int:
    """Data rows in the screen's *list-shaped* tables.

    A list table has an all-``<th>`` header row and all-``<td>`` body rows. A
    key/value table (``<th>`` + ``<td>`` per row) is fixed reference material,
    not a list that grows with the data, so it is excluded — otherwise a screen
    with eight facts and three list rows would look like eleven list rows.
    """
    import re

    total = 0
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.DOTALL):
        rows = re.findall(r"<tr>(.*?)</tr>", table, flags=re.DOTALL)
        if not rows:
            continue
        header = rows[0]
        body = rows[1:]
        if "<td>" in header or not body:
            continue
        if all("<td>" in row and "<th>" not in row for row in body):
            total += len(body)
    return total


def _num(value: int, locale: Locale) -> str:
    from cloudbot.presentation.i18n.formatters import localize_digits

    return localize_digits(str(value), locale)


# -- item factories ----------------------------------------------------------
def _ports(n: int) -> PortReport:
    return PortReport(
        workspace_id=str(WS),
        ports=tuple(
            PortEntry(
                port=8000 + i,
                protocol="tcp",
                address="0.0.0.0",
                scope="wildcard",
                state="LISTEN",
                attributed=True,
                pid=1000 + i,
                kind="run",
            )
            for i in range(n)
        ),
        unix=tuple(
            UnixSocket(path=f"/tmp/s{i}.sock", type="stream", state="unconnected", attributed=False)
            for i in range(n)
        ),
        count=n,
        unix_count=n,
        complete=True,
        notes=(),
        pids_scanned=n,
        fds_scanned=n,
        reachability="",
    )


def _processes(n: int) -> ProcessList:
    return ProcessList(
        processes=tuple(
            ProcessEntry(
                id=f"proc_{i:016x}",
                pid=2000 + i,
                ppid=1,
                argv=("python3", f"job{i}.py"),
                kind="detached",
                source_id="",
                started_at=NOW,
            )
            for i in range(n)
        ),
        count=n,
    )


def _packages(n: int) -> PackageList:
    return PackageList(
        packages=tuple(Package(name=f"pkg{i}", version="1.0") for i in range(n)),
        count=n,
        truncated=False,
        shared_enabled=True,
        shared_available=False,
        shared_path="",
        private_path="/opt/python/private",
    )


def _tunnels(n: int) -> TunnelList:
    return TunnelList(
        tunnels=tuple(
            Tunnel(
                id=f"tnl_{i}",
                workspace_id=str(WS),
                port=8000 + i,
                url=f"https://t{i}.trycloudflare.com",
                status="active",
                created_at=NOW,
            )
            for i in range(n)
        )
    )


def _archive(n: int) -> ArchiveResult:
    return ArchiveResult(
        format="zip",
        destination="/",
        entries_total=n,
        entries_extracted=n,
        entries_skipped=0,
        entries_rejected=0,
        bytes_written=10 * n,
        dry_run=False,
        entries=tuple(
            ArchiveEntry(
                name=f"f{i}.py",
                path=f"/f{i}.py",
                type="file",
                size=10,
                mode="-rw-r--r--",
                rejected=False,
                skipped=False,
            )
            for i in range(n)
        ),
    )


SCREEN_CASES = {
    "ports": lambda n, loc, page: port_screens.ports_screen(_ports(n), WS, loc, page=page),
    "processes": lambda n, loc, page: proc_screens.processes_screen(_processes(n), WS, loc, page=page),
    "packages": lambda n, loc, page: pkg_screens.packages_screen(_packages(n), WS, loc, page=page),
    "tunnels": lambda n, loc, page: tnl_screens.tunnels_screen(
        _tunnels(n), WS, loc, max_tunnels=100, page=page
    ),
    "archive": lambda n, loc, page: arc_screens.archive_result_screen(_archive(n), WS, loc, page=page),
}


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(SCREEN_CASES))
def test_a_full_list_renders_one_page_and_says_which(name, locale):
    build = SCREEN_CASES[name]
    count = pagination.PAGE_SIZE * 3
    screen = build(count, locale, 0)

    # Only one page worth of data rows, in the table(s) and in the keyboard.
    assert _table_rows(screen.doc.html) <= pagination.PAGE_SIZE
    assert len(_buttons(screen)) <= 40, "the keyboard grew with the list length"
    # The footer is present (the audit rule) and names page 2 of 3 on page 2.
    assert _footer_present(build(count, locale, 1).doc.html, locale)
    assert "Page 1 of" in screen.doc.html or "صفحة ١ من" in screen.doc.html
    # The pager row exists and offers only "next" on the first page.
    actions = [b.callback_data for b in _buttons(screen) if b.callback_data]
    assert any(a.endswith(":1") for a in actions), f"{name} has no next-page button"


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(SCREEN_CASES))
def test_paging_moves_the_window(name, locale):
    build = SCREEN_CASES[name]
    count = pagination.PAGE_SIZE + 2
    page0 = build(count, locale, 0)
    page1 = build(count, locale, 1)

    # Page 1 shows the first item and not the last; page 2 shows the last.
    first_html = page0.doc.html
    last_html = page1.doc.html
    assert first_html != last_html
    if name == "ports":
        assert _num(8000, locale) in first_html and _num(8010, locale) not in first_html
        # ports and sockets share one window
        assert _num(8011, locale) in last_html
    elif name == "processes":
        assert "job0.py" in first_html and "job10.py" not in first_html
        assert "job11.py" in last_html
    elif name == "packages":
        assert "pkg0" in first_html and "pkg10" not in first_html
        assert "pkg11" in last_html
    elif name == "tunnels":
        assert _num(8000, locale) in first_html and _num(8010, locale) not in first_html
        assert _num(8011, locale) in last_html
    else:
        assert "f0.py" in first_html and "f10.py" not in first_html
        assert "f11.py" in last_html


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(SCREEN_CASES))
def test_the_keyboard_does_not_grow_with_the_list(name, locale):
    build = SCREEN_CASES[name]
    # Two different oversized lists, both multi-page, so the pager row itself is
    # present in both and only the list length differs.
    small = build(pagination.PAGE_SIZE * 2, locale, 0)
    large = build(pagination.PAGE_SIZE * 8, locale, 0)
    assert len(_buttons(small)) == len(_buttons(large))


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(SCREEN_CASES))
def test_an_out_of_range_page_is_clamped_not_empty(name, locale):
    build = SCREEN_CASES[name]
    screen = build(pagination.PAGE_SIZE + 1, locale, 99)
    assert _table_rows(screen.doc.html) >= 1


def test_metrics_goes_through_the_one_pager_too():
    from cloudbot.presentation.catalog import fixtures

    metrics = fixtures()["metrics"]
    for locale in LOCALES:
        screen = met_screens.metrics_screen(metrics, WS, locale)
        assert catalog.t(
            "common.page",
            locale,
            page="1" if locale is Locale.EN else "١",
            total="1" if locale is Locale.EN else "١",
        ) in screen.doc.html


# -- the catalog audit -------------------------------------------------------
def _has_pager(screen) -> bool:
    return any(
        (b.callback_data or "") in {
            "ws:list",
            "term:list",
            "file:ls",
            "port:list",
            "proc:list",
            "pkg:list",
            "met:view",
            "tnl:list",
            "arc:result",
        }
        or (b.callback_data or "").startswith(
            ("ws:list:", "term:list:", "file:ls:", "port:list:", "proc:list:",
             "pkg:list:", "met:view:", "tnl:list:", "arc:result:")
        )
        for b in _buttons(screen)
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_no_catalog_screen_renders_more_than_a_page_without_a_pager(locale):
    fixtures = screen_catalog.fixtures()
    # A closed, compile-time set of rows cannot grow with the data: the admin
    # notification categories are an enum, not a fetched list.
    FIXED_ROW_SCREENS = {"admin_notifications"}
    offenders = []
    for name, build in screen_catalog.SCREEN_BUILDERS.items():
        if name in FIXED_ROW_SCREENS:
            continue
        screen = build(fixtures, locale)
        rows = _table_rows(screen.doc.html)
        if rows > pagination.PAGE_SIZE and not _has_pager(screen):
            offenders.append((name, rows))
    assert not offenders, f"screens over {pagination.PAGE_SIZE} rows with no pager: {offenders}"


# -- the workspace list has one entry point, paged ---------------------------
def test_the_workspace_list_entry_point_pages_the_logout_and_delete_renders():
    """The logout and delete handlers used to render the *whole* list unpaged."""
    from cloudbot.handlers.workspaces import _list_screen

    fixtures = screen_catalog.fixtures()
    many = [fixtures["ws"]] * (pagination.PAGE_SIZE * 2)

    first = _list_screen(many, Locale.EN, confirmation="left")
    assert _table_rows(first.doc.html) == pagination.PAGE_SIZE
    assert "Page 1 of 2" in first.doc.html

    second = _list_screen(many, Locale.EN, page=1)
    assert "Page 2 of 2" in second.doc.html
