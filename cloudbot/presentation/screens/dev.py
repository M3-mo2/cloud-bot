"""Developer-tools screens (TASK 24): the in-bot, admin-only button inspector.

These screens are pure functions of data + locale, like every other screen. The
*data* (a rendered screen, its button verdicts, a walk report) comes from
``presentation.catalog``; nothing here touches the engine, Telegram, or the
dispatcher — the handler wires those.

The callback namespace is ``dev:`` and is built/parsed here (the one codec for
``dev:``), mirroring ``terminal_callbacks`` / ``admin/callbacks``. It carries a
screen **name** and a locale, both colon-free, so the payload stays inline and
restart-proof.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudbot.core.locale import Locale
from cloudbot.presentation.catalog import ButtonVerdict, ScreenVerdict, WalkReport
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import localize_digits
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import Html, esc, italic
from cloudbot.presentation.screens.screen import Screen

DEV_PREFIX = "dev"

#: Verdict → its catalog key for the short label.
VERDICT_KEY: dict[str, str] = {
    "handled": "dev.verdict.handled",
    "no_handler": "dev.verdict.no_handler",
    "stale": "dev.verdict.stale",
    "unparseable": "dev.verdict.unparseable",
    "url": "dev.verdict.url",
    "render_error": "dev.verdict.render_error",
}

#: The actionable, non-ok verdicts the report lists.
BAD_VERDICTS = ("no_handler", "stale", "unparseable", "render_error")


def _n(value: int, locale: Locale) -> str:
    return localize_digits(str(value), locale)


# -- callback builders ---------------------------------------------------------
def sections_callback() -> str:
    return f"{DEV_PREFIX}:sections"


def section_callback(key: str) -> str:
    return f"{DEV_PREFIX}:section:{key}"


def inspect_callback(name: str, locale: Locale) -> str:
    return f"{DEV_PREFIX}:inspect:{name}:{locale.value}"


def tap_callback(name: str, locale: Locale, index: int) -> str:
    return f"{DEV_PREFIX}:tap:{name}:{locale.value}:{int(index)}"


def run_section_callback(key: str) -> str:
    return f"{DEV_PREFIX}:run:section:{key}"


def run_catalog_callback() -> str:
    return f"{DEV_PREFIX}:run:catalog"


# -- callback parsing ----------------------------------------------------------
@dataclass(frozen=True)
class DevCallback:
    action: str
    args: tuple[str, ...] = ()
    valid: bool = True


def _invalid(action: str = "", *args: str) -> DevCallback:
    return DevCallback(action=action, args=tuple(args), valid=False)


def parse(data: str) -> DevCallback:
    """Parse any ``dev:`` callback. Total: never raises."""
    if not isinstance(data, str) or not data.startswith(DEV_PREFIX + ":"):
        return _invalid()
    parts = data.split(":")
    if len(parts) < 2 or not parts[1]:
        return _invalid()
    action, rest = parts[1], tuple(parts[2:])
    if action == "sections":
        return DevCallback(action) if not rest else _invalid(action, *rest)
    if action == "section":
        return DevCallback(action, rest) if len(rest) == 1 and rest[0] else _invalid(action, *rest)
    if action == "inspect":
        if len(rest) == 2 and rest[0] and rest[1] in ("en", "ar"):
            return DevCallback(action, rest)
        return _invalid(action, *rest)
    if action == "tap":
        if len(rest) == 3 and rest[0] and rest[1] in ("en", "ar") and rest[2].isdigit():
            return DevCallback(action, rest)
        return _invalid(action, *rest)
    if action == "run":
        if rest == ("catalog",):
            return DevCallback(action, rest)
        if len(rest) == 2 and rest[0] == "section" and rest[1]:
            return DevCallback(action, rest)
        return _invalid(action, *rest)
    return _invalid(action, *rest)


def _locale_of(value: str) -> Locale:
    return Locale.AR if value == "ar" else Locale.EN


# -- screens -------------------------------------------------------------------
def sections_screen(stats: list[tuple[str, int, int]], locale: Locale) -> Screen:
    """Screen 1 — every section that has screens, with its screen/button counts."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("dev.sections.title", locale))
    doc.p(catalog.t("dev.sections.hint", locale))
    rows = []
    for key, screen_count, button_count in stats:
        rows.append(
            [
                catalog.t("dev.section." + key, locale),
                _n(screen_count, locale),
                _n(button_count, locale),
            ]
        )
    doc.table(
        [
            catalog.t("dev.col.section", locale),
            catalog.t("dev.col.screens", locale),
            catalog.t("dev.col.buttons", locale),
        ],
        rows,
    )
    markup = inline_markup(
        [
            [inline(catalog.t("dev.section." + key, locale), section_callback(key), intent=Intent.PRIMARY)]
            for key, _s, _b in stats
        ]
    )
    return Screen(doc=doc, markup=markup)


def section_screen(key: str, names: list[str], locale: Locale) -> Screen:
    """Screen 2 — every screen in a section (both locales reachable from here)."""
    doc = RichDoc(locale)
    doc.h(2, f"{catalog.t('dev.section.' + key, locale)} — {catalog.t('dev.section.screens', locale)}")
    doc.p(catalog.t("dev.section.hint", locale))
    doc.ul([Html(f"<code>{esc(name)}</code>") for name in names])
    rows = []
    for start in range(0, len(names), 2):
        row = []
        for name in names[start : start + 2]:
            row.append(inline(f"🔎 {name}", inspect_callback(name, locale), intent=Intent.PRIMARY))
        rows.append(row)
    rows.append(
        [
            inline(catalog.t("dev.run.section", locale), run_section_callback(key), intent=Intent.SUCCESS),
        ]
    )
    rows.append([inline(catalog.t("dev.back.sections", locale), sections_callback(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(rows))


def _verdict_label(verdict: str, locale: Locale) -> str:
    return catalog.t(VERDICT_KEY.get(verdict, "dev.verdict.unknown"), locale)


def _state_label(
    text: str, style: str | None, disabled: bool, locale: Locale
) -> str:
    parts = []
    parts.append(text)
    if style:
        parts.append(f"[{style}]")
    if disabled:
        parts.append(catalog.t("dev.button.disabled", locale))
    return " ".join(parts)


def inspect_screen(
    name: str,
    real_html: str,
    real_rtl: bool,
    verdicts: tuple[ButtonVerdict, ...],
    locale: Locale,
    *,
    error: str | None = None,
) -> Screen:
    """Screen 3 — one screen inspected: the real body + the live button table.

    ``real_html``/``real_rtl`` are the screen exactly as the user sees it.
    """
    doc = RichDoc(locale)
    doc.h(2, catalog.t("dev.inspect.title", locale, name=name))
    doc.div()
    if error is not None:
        doc.alert("error", catalog.t("dev.inspect.render_error", locale), error)
    else:
        # The real screen body, verbatim (trusted markup from our own renderer).
        doc.p(italic(catalog.t("dev.inspect.as_user", locale)))
        doc.raw(real_html)
    doc.div()
    doc.h(3, catalog.t("dev.button.table_title", locale))

    toggled = Locale.AR if locale is Locale.EN else Locale.EN
    toggle_label = catalog.t(
        "dev.inspect.show_" + toggled.value, locale
    )

    if not verdicts:
        doc.p(catalog.t("dev.button.none", locale))
    else:
        rows = []
        for verdict in verdicts:
            button = verdict.button
            target = button.data or (
                catalog.t("dev.url_redacted", locale) if button.url else "—"
            )
            rows.append(
                [
                    _n(button.index, locale),
                    _state_label(button.text, button.style, button.disabled, locale),
                    _raw_code(target),
                    _verdict_label(verdict.verdict, locale),
                ]
            )
        doc.table(
            [
                catalog.t("dev.col.index", locale),
                catalog.t("dev.col.button", locale),
                catalog.t("dev.col.callback", locale),
                catalog.t("dev.col.verdict", locale),
            ],
            rows,
        )

    rows = []
    for verdict in verdicts:
        rows.append(
            [
                inline(
                    f"▶️ #{verdict.button.index} {verdict.button.text}",
                    tap_callback(name, locale, verdict.button.index),
                    intent=Intent.PRIMARY,
                    disabled=verdict.button.data is None and verdict.button.url is not None,
                )
            ]
        )
    rows.append(
        [
            inline(toggle_label, inspect_callback(name, toggled), intent=Intent.PRIMARY),
        ]
    )
    section = name
    rows.append(
        [
            inline(
                catalog.t("dev.run.section", locale),
                run_section_callback(_section_key_of(name)),
                intent=Intent.SUCCESS,
            ),
            inline(catalog.t("dev.run.catalog", locale), run_catalog_callback(), intent=Intent.SUCCESS),
        ]
    )
    rows.append(
        [
            inline(
                catalog.t("dev.back.section", locale),
                section_callback(_section_key_of(name)),
                intent=Intent.PRIMARY,
            )
        ]
    )
    return Screen(doc=doc, markup=inline_markup(rows))


def tap_result_screen(
    name: str,
    locale: Locale,
    *,
    rendered_html: str | None,
    error: str | None = None,
    no_render: bool = False,
) -> Screen:
    """The real next screen after a tap (or the reason there was none)."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("dev.tap.title", locale, name=name))
    if error is not None:
        doc.alert("error", catalog.t("dev.tap.error", locale), error)
    elif no_render:
        doc.alert(
            "warning",
            catalog.t("dev.tap.no_render_title", locale),
            catalog.t("dev.tap.no_render_body", locale),
        )
    elif rendered_html is not None:
        doc.p(italic(catalog.t("dev.tap.result", locale)))
        doc.raw(rendered_html)
    markup = inline_markup(
        [
            [inline(catalog.t("dev.back.screen", locale), inspect_callback(name, locale), intent=Intent.PRIMARY)],
            [inline(catalog.t("dev.back.sections", locale), sections_callback(), intent=Intent.PRIMARY)],
        ]
    )
    return Screen(doc=doc, markup=markup)


def report_screen(report: WalkReport, locale: Locale, *, scope: str) -> Screen:
    """The result of ``run all checks``: totals + every failure listed."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("dev.report.title", locale))
    doc.p(catalog.t("dev.report.scope", locale, scope=scope))
    doc.kv_table(
        [
            (catalog.t("dev.col.screens", locale), _n(report.screens, locale)),
            (catalog.t("dev.col.buttons", locale), _n(report.buttons, locale)),
            (catalog.t("dev.verdict.handled", locale), _n(report.totals.get("handled", 0), locale)),
            (catalog.t("dev.verdict.no_handler", locale), _n(report.totals.get("no_handler", 0), locale)),
            (catalog.t("dev.verdict.stale", locale), _n(report.totals.get("stale", 0), locale)),
            (catalog.t("dev.verdict.unparseable", locale), _n(report.totals.get("unparseable", 0), locale)),
            (catalog.t("dev.verdict.render_error", locale), _n(report.totals.get("render_error", 0), locale)),
            (catalog.t("dev.verdict.url", locale), _n(report.totals.get("url", 0), locale)),
        ]
    )
    if report.failures:
        doc.h(3, catalog.t("dev.report.failures", locale, n=_n(len(report.failures), locale)))
        items = []
        for screen_name, verdict, index, label in report.failures:
            loc_label = _verdict_label(verdict, locale)
            if index >= 0:
                items.append(
                    Html(
                        f"<code>{esc(screen_name)}</code> · #{index} {esc(label)} — "
                        f"{esc(loc_label)}"
                    )
                )
            else:
                items.append(
                    Html(f"<code>{esc(screen_name)}</code> — {esc(loc_label)}: {esc(label)}")
                )
        doc.ul(items)
    else:
        doc.p(catalog.t("dev.report.no_failures", locale))
    markup = inline_markup(
        [[inline(catalog.t("dev.back.sections", locale), sections_callback(), intent=Intent.PRIMARY)]]
    )
    return Screen(doc=doc, markup=markup)


def disabled_screen(locale: Locale) -> Screen:
    """Shown when the developer tools are switched off."""
    doc = RichDoc(locale)
    doc.h(2, catalog.t("dev.disabled.title", locale))
    doc.p(catalog.t("dev.disabled.body", locale))
    return Screen(doc=doc)


def _raw_code(value: str) -> Html:
    return Html(f"<code>{esc(value)}</code>")


def _section_key_of(name: str) -> str:
    from cloudbot.presentation import catalog as screen_catalog

    return screen_catalog.section_of(name)


__all__ = [
    "BAD_VERDICTS",
    "DEV_PREFIX",
    "DevCallback",
    "disabled_screen",
    "inspect_callback",
    "inspect_screen",
    "parse",
    "report_screen",
    "run_catalog_callback",
    "run_section_callback",
    "section_callback",
    "section_screen",
    "sections_callback",
    "sections_screen",
    "tap_callback",
    "tap_result_screen",
]
