"""Admin section: «💾 النسخ الاحتياطي» — a full backup console.

Pure screens (no I/O): they render ``AdminView.backup``. Every action is a codec
builder, so the contract test proves each button is parseable and routable.

The files list is **blocks** — a bold name per backup, one fact line, and that
file's own *Restore* button straight under it — not a four-column table. The
schedule/data identity blocks stay two-column ``label | value`` tables, which is
the only shape a table may hold here.
"""

from __future__ import annotations

from cloudbot.domain.admin import AdminView
from cloudbot.domain.backup import DEFAULT_TZ, BackupConfig, BackupFile, BackupView
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.i18n.formatters import format_bytes, format_datetime
from cloudbot.presentation.keyboards.buttons import inline
from cloudbot.presentation.keyboards.markup import inline_markup
from cloudbot.presentation.keyboards.styles import Intent
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.rich.tags import code, fragment, italic
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin._helpers import (
    add_block_header,
    add_html_header,
    num,
    subject,
)
from cloudbot.presentation.screens.screen import Screen

from .section import AdminSection, register


def _when(value, locale) -> str:
    return format_datetime(value, locale) if value is not None else "—"


_PATH_SLOT = "\x00"


def _hint_parts(locale) -> tuple[str, str]:
    """The directory hint split around its path, so the path can be inline code
    (a catalog template is plain text and cannot carry markup)."""
    text = catalog.t("admin.bk.files_hint", locale, path=_PATH_SLOT)
    before, _, after = text.partition(_PATH_SLOT)
    return before, after


def _before_hint(locale) -> str:
    return _hint_parts(locale)[0]


def _after_hint(locale) -> str:
    return _hint_parts(locale)[1]


def _period(config: BackupConfig, locale) -> str:
    if config.period_kind == "daily":
        return catalog.t(
            "admin.bk.period_daily", locale, time=config.daily_time, tz=config.tz_name
        )
    return catalog.t("admin.bk.period_hours", locale, hours=num(config.period_hours, locale))


def _schedule_fact(config: BackupConfig, locale) -> str:
    if not config.enabled:
        return catalog.t("admin.bk.off_short", locale)
    if config.next_run_at is not None:
        return catalog.t("admin.bk.next_short", locale, when=_when(config.next_run_at, locale))
    return catalog.t("admin.bk.next_none_short", locale)


def backup_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    backup = view.backup

    if backup is None:
        add_block_header(doc, subject(locale, "admin.bk.title"), "—", locale)
        doc.paragraph(catalog.t("admin.overview.unavailable", locale))
        return Screen(
            doc=doc,
            markup=inline_markup(
                [[inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)]]
            ),
        )
    config = backup.config
    files = backup.files

    # The header's second line is the one fact that matters on this screen: the
    # schedule's next run (or that it is off). A screen whose subject is *when the
    # next backup happens* does not need an invented count.
    add_block_header(
        doc,
        subject(locale, "admin.bk.title"),
        _schedule_fact(config, locale),
        locale,
    )

    doc.quote(catalog.t("admin.bk.on" if config.enabled else "admin.bk.off", locale))
    if config.enabled:
        doc.paragraph(catalog.t("admin.bk.next", locale, when=_when(config.next_run_at, locale)))
    if config.last_run_at is not None:
        doc.paragraph(catalog.t("admin.bk.last", locale, when=_when(config.last_run_at, locale)))
    else:
        doc.paragraph(catalog.t("admin.bk.last_none", locale))
    if config.last_error:
        doc.paragraph(
            catalog.t(
                "admin.bk.last_error",
                locale,
                error=catalog.t(f"admin.bk.refuse.{config.last_error}", locale)
                if config.last_error != "failed"
                else config.last_error,
            )
        )

    doc.kv_block(
        [
            (catalog.t("admin.bk.col_schedule", locale), _period(config, locale)),
            (
                catalog.t("admin.bk.col_retention", locale),
                num(config.retention_keep, locale),
            ),
            (
                catalog.t("admin.bk.col_destination", locale),
                catalog.t("admin.bk.dest_here", locale)
                if config.destination_chat_id is None
                else catalog.t(
                    "admin.bk.destination_other",
                    locale,
                    chat=num(config.destination_chat_id, locale),
                ),
            ),
        ]
    )
    doc.paragraph(
        catalog.t(
            "admin.bk.schema",
            locale,
            file=num(backup.schema_version, locale),
            code=num(backup.code_schema_version, locale),
        )
    )

    doc.heading(
        f"✦ {catalog.t('admin.bk.files_title', locale)} ⟸ {num(len(files), locale)}",
        size=2,
    )
    if files:
        for index, item in enumerate(files[:8]):
            _file_block(doc, item, locale)
    else:
        doc.paragraph(catalog.t("admin.bk.files_empty", locale))
    if any(item.format == "legacy-sqlite" for item in files):
        doc.paragraph(catalog.t("admin.bk.legacy_note", locale))
    doc.paragraph(f"{_before_hint(locale)}{backup.directory or 'data/backups'}{_after_hint(locale)}")

    kb = []
    kb.extend(
        [
            [
                inline(
                    catalog.t("admin.bk.instant", locale),
                    admin_cb.backup_instant("full"),
                    intent=Intent.SUCCESS,
                ),
                inline(
                    catalog.t("admin.bk.instant_settings", locale),
                    admin_cb.backup_instant("settings"),
                    intent=Intent.PRIMARY,
                ),
            ],
            [
                inline(
                    catalog.t("admin.bk.schedule", locale),
                    admin_cb.backup_schedule(),
                    intent=Intent.PRIMARY,
                ),
                inline(
                    catalog.t("admin.bk.switch_off" if config.enabled else "admin.bk.switch_on", locale),
                    admin_cb.backup_switch(not config.enabled),
                    intent=Intent.DANGER if config.enabled else Intent.SUCCESS,
                ),
            ],
            [
                inline(
                    catalog.t("admin.bk.restore_action", locale),
                    admin_cb.backup_restore(),
                    intent=Intent.DANGER,
                ),
                inline(
                    catalog.t("admin.bk.retention_action", locale),
                    admin_cb.backup_prune(),
                    intent=Intent.PRIMARY,
                ),
            ],
        ]
    )
    kb.append([inline(catalog.t("admin.back", locale), admin_cb.menu(), intent=Intent.PRIMARY)])
    return Screen(doc=doc, markup=inline_markup(kb))


def _file_block(doc: RichDoc, item: BackupFile, locale) -> None:
    """One backup file: name (bold), its format/size/time, and its own restore button."""
    doc.heading(item.name, size=3)
    facts = [
        catalog.t(f"admin.bk.scope.{item.scope}", locale),
        format_bytes(item.size_bytes, locale),
        _when(item.created_at, locale),
    ]
    if item.format:
        label = catalog.t(f"admin.bk.format.{item.format}", locale)
        facts.insert(1, label if catalog.has(f"admin.bk.format.{item.format}", locale) else item.format)
    doc.paragraph(" · ".join(facts))
    # A legacy SQLite archive is history: restore refuses it into Postgres, so
    # the button is not offered at all.
    if item.format == "legacy-sqlite":
        return
    action = admin_cb.backup_disk(item.name)
    # A filename long enough to overflow Telegram's 64-byte callback cap simply
    # gets no per-file button; the upload/restore path still reaches it.
    if len(action.encode("utf-8")) <= 64:
        doc.buttons(
            [
                (
                    catalog.t("admin.bk.restore_file", locale, file=_short(item.name)),
                    action,
                    "primary",
                )
            ]
        )


def _short(name: str, limit: int = 30) -> str:
    return name if len(name) <= limit else f"…{name[-limit:]}"


def schedule_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    backup = view.backup
    config = backup.config if backup else BackupConfig()
    add_html_header(
        doc,
        subject(locale, "admin.bk.schedule_title"),
        _period(config, locale),
        locale,
    )
    doc.p(catalog.t("admin.bk.schedule_hint", locale))
    doc.kv_table(
        [
            (catalog.t("admin.bk.schedule", locale), _period(config, locale)),
            (
                catalog.t("admin.bk.files_title", locale),
                catalog.t("admin.bk.retention", locale, n=num(config.retention_keep, locale)),
            ),
            (
                catalog.t("admin.bk.destination", locale),
                catalog.t("admin.bk.destination", locale)
                if config.destination_chat_id is None
                else catalog.t(
                    "admin.bk.destination_other",
                    locale,
                    chat=num(config.destination_chat_id, locale),
                ),
            ),
            (catalog.t("admin.bk.col_last", locale), _when(config.last_run_at, locale)),
            (catalog.t("admin.bk.col_next", locale), _when(config.next_run_at, locale)),
        ]
    )

    def chip(key: str, action: str, intent: Intent = Intent.PRIMARY):
        return inline(catalog.t(key, locale), action, intent=intent)

    kb = [
        [
            chip("admin.bk.hours_1", admin_cb.backup_hours(1)),
            chip("admin.bk.hours_6", admin_cb.backup_hours(6)),
        ],
        [
            chip("admin.bk.hours_12", admin_cb.backup_hours(12)),
            chip("admin.bk.hours_24", admin_cb.backup_hours(24)),
        ],
        [
            chip("admin.bk.daily_03", admin_cb.backup_daily("03:00")),
            chip("admin.bk.daily_04", admin_cb.backup_daily("04:00")),
        ],
        [
            chip("admin.bk.retention_3", admin_cb.backup_retention(3)),
            chip("admin.bk.retention_7", admin_cb.backup_retention(7)),
        ],
        [
            chip("admin.bk.retention_14", admin_cb.backup_retention(14)),
            chip("admin.bk.retention_30", admin_cb.backup_retention(30)),
        ],
        [
            chip("admin.bk.dest_here", admin_cb.backup_destination(view.admin_id or 0)),
        ],
        [
            inline(
                catalog.t("admin.back", locale),
                admin_cb.section("backup"),
                intent=Intent.PRIMARY,
            )
        ],
    ]
    return Screen(doc=doc, markup=inline_markup(kb))


def restore_prompt_screen(view: AdminView) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    add_html_header(doc, subject(locale, "admin.bk.restore_prompt_title"), "—", locale)
    doc.p(catalog.t("admin.bk.restore_prompt", locale))
    return Screen(
        doc=doc,
        markup=inline_markup(
            [
                [
                    inline(
                        catalog.t("admin.back", locale),
                        admin_cb.section("backup"),
                        intent=Intent.PRIMARY,
                    )
                ]
            ]
        ),
    )


def restore_confirm_screen(
    view: AdminView,
    *,
    token: str,
    scope: str,
    label: str,
) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    add_html_header(
        doc,
        subject(locale, "admin.bk.restore_confirm_title"),
        catalog.t("admin.bk.scope_fact", locale, scope=catalog.t(f"admin.bk.scope.{scope}", locale)),
        locale,
    )
    doc.blockquote(
        fragment(
            catalog.t(
                "admin.bk.restore_confirm_settings"
                if scope == "settings"
                else "admin.bk.restore_confirm",
                locale,
                what=catalog.t(
                    "admin.bk.restore_what_settings"
                    if scope == "settings"
                    else "admin.bk.restore_what_full",
                    locale,
                ),
                file=code(label),
            )
        )
    )
    kb = [
        [
            inline(
                catalog.t("admin.bk.restore_go", locale),
                admin_cb.backup_go(token, scope),
                intent=Intent.DANGER,
            )
        ],
        [
            inline(
                catalog.t("admin.bk.restore_cancel", locale),
                admin_cb.backup_cancel(token),
                intent=Intent.PRIMARY,
            )
        ],
    ]
    return Screen(doc=doc, markup=inline_markup(kb))


def restore_result_screen(
    view: AdminView,
    *,
    ok: bool,
    reason: str = "",
    scope: str = "full",
    safety: str = "",
) -> Screen:
    locale = view.locale
    doc = RichDoc(locale)
    add_html_header(
        doc,
        subject(locale, "admin.bk.restore_title"),
        catalog.t("admin.bk.restore_ok_short" if ok else "admin.bk.restore_refused_short", locale),
        locale,
    )
    if ok:
        doc.blockquote(
            fragment(
                catalog.t(
                    "admin.bk.restore_done_settings"
                    if scope == "settings"
                    else "admin.bk.restore_done",
                    locale,
                    what=catalog.t(
                        "admin.bk.restore_what_settings"
                        if scope == "settings"
                        else "admin.bk.restore_what_full",
                        locale,
                    ),
                )
            )
        )
        if safety:
            doc.p(catalog.t("admin.bk.restore_safety", locale, path=code(safety)))
    else:
        doc.blockquote(
            fragment(
                catalog.t(
                    "admin.bk.restore_refused",
                    locale,
                    reason=catalog.t(f"admin.bk.refuse.{reason}", locale)
                    if catalog.has(f"admin.bk.refuse.{reason}", locale)
                    else catalog.t("admin.bk.refuse.unknown", locale),
                )
            )
        )
    kb = [
        [
            inline(
                catalog.t("admin.back", locale),
                admin_cb.section("backup"),
                intent=Intent.PRIMARY,
            ),
            inline(
                catalog.t("admin.bk.restore_action", locale),
                admin_cb.backup_restore(),
                intent=Intent.PRIMARY,
            ),
        ]
    ]
    return Screen(doc=doc, markup=inline_markup(kb))


def received_notice(locale, *, size: str, scope: str):
    """The upload's one-line banner (shared by the handler, not duplicated)."""
    return fragment(
        catalog.t(
            "admin.bk.upload_received",
            locale,
            size=size,
            scope=catalog.t(f"admin.bk.scope.{scope}", locale),
        )
    )


def notice_screen(view: AdminView, *, key: str, **args) -> Screen:
    """A one-line result above the section (backup made, pruned, refused)."""
    locale = view.locale
    screen = backup_screen(view)
    screen.doc.prepend_text(catalog.t(key, locale, **args))
    return screen


register(
    AdminSection(
        key="backup",
        title_key="admin.section.backup",
        order=38,
        icon="💾",
        build=backup_screen,
    )
)
