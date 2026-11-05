"""Deterministic fixtures for the developer-tools screens' goldens.

The dev screens are rendered by the ``/dev`` handler from live data (the sandbox's
button verdicts), which is intentionally not byte-stable. For the **goldens** we
need fixed inputs, so this module builds a representative inspect/report screen
from the catalog's own fixtures — the same code path, just a fixed walk result.

It lives in ``cloudbot.presentation`` (not in the catalog registry) so the tool
does not list and walk its own screens.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.screens import dev as dev_screens
from cloudbot.presentation.screens.screen import Screen


async def _claims(_data: str) -> bool:
    """A stand-in for the dispatcher's routing: every callback is claimed, so the
    golden inspect screen shows a stable, all-handled button table."""
    return True


def inspect_terminal_view(locale: Locale) -> Screen:
    """A fixed inspect screen for the richest terminal view (the historical bug)."""
    locale_of_screen = Locale.EN
    verdict = screen_catalog.ScreenVerdict(
        name="terminal_view_running",
        section="terminal",
        locale=locale_of_screen,
        buttons=tuple(
            screen_catalog.ButtonVerdict(
                button=info, verdict=screen_catalog.classify(info, claimed=True)
            )
            for info in screen_catalog.button_infos(
                screen_catalog.build("terminal_view_running", locale_of_screen)
            )
        ),
    )
    body = screen_catalog.build("terminal_view_running", locale_of_screen)
    return dev_screens.inspect_screen(
        "terminal_view_running",
        body.doc.html,
        body.doc.build().is_rtl,
        verdict.buttons,
        locale,
    )


def report_terminal(locale: Locale) -> Screen:
    """A fixed run-all-checks report over the terminal section (both locales)."""
    verdicts = []
    for loc in (Locale.EN, Locale.AR):
        for spec in screen_catalog.screens_in("terminal"):
            verdicts.append(
                screen_catalog.ScreenVerdict(
                    name=spec.name,
                    section="terminal",
                    locale=loc,
                    buttons=tuple(
                        screen_catalog.ButtonVerdict(
                            button=info, verdict=screen_catalog.classify(info, claimed=True)
                        )
                        for info in screen_catalog.button_infos(
                            screen_catalog.build(spec.name, loc)
                        )
                    ),
                )
            )
    totals = {"handled": 0, "no_handler": 0, "stale": 0, "unparseable": 0, "url": 0, "render_error": 0}
    buttons = 0
    for v in verdicts:
        for b in v.buttons:
            buttons += 1
            totals[b.verdict] = totals.get(b.verdict, 0) + 1
    report = screen_catalog.WalkReport(
        locale=Locale.EN, screens=len(verdicts), buttons=buttons, totals=totals, failures=()
    )
    return dev_screens.report_screen(report, locale, scope="terminal")


def sections(locale: Locale) -> Screen:
    """A fixed sections screen (counts derived from the frozen catalog)."""
    stats = []
    for section in screen_catalog.sections():
        specs = screen_catalog.screens_in(section.key)
        buttons = sum(
            len(screen_catalog.button_infos(screen_catalog.build(spec.name, locale)))
            for spec in specs
        )
        stats.append((section.key, len(specs), buttons))
    return dev_screens.sections_screen(stats, locale)


#: A fixed builder table for the golden suite (name → builder).
SCREEN_BUILDERS = {
    "dev_sections": sections,
    "dev_inspect": inspect_terminal_view,
    "dev_report": report_terminal,
}
