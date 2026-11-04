"""File handlers: browse, view, mkdir, rename, delete, upload."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cloudbot.core.errors import InvalidInput
from cloudbot.handlers.render import show_screen
from cloudbot.middleware.context import UserContext
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import decode
from cloudbot.presentation.rich.sender import RichSender
from cloudbot.presentation.screens import files as file_screens
from cloudbot.services.bundle import Services
from cloudbot.states.input import Input

router = Router(name="files")


def _wid(text: str):
    from cloudbot.domain.value_objects import WorkspaceId

    return WorkspaceId(text)


async def _show_listing(
    event, ctx: UserContext, services: Services, sender: RichSender, ws_id, path: str, page: int = 0
) -> None:
    offset = page * file_screens.PAGE_SIZE
    page_obj = await services.files.list(ctx.tg_id, ws_id, path, offset=offset)
    screen = file_screens.file_browser(page_obj, ws_id, ctx.locale, page_index=page)
    await show_screen(event, screen, ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("file:ls"))
async def on_ls(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    page = int(decoded.key[1]) if len(decoded.key) > 1 else 0
    await _show_listing(cb, ctx, services, sender, ws_id, "/", page)


@router.callback_query(F.data.startswith("file:cd"))
async def on_cd(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    path = decoded.key[1]
    await _show_listing(cb, ctx, services, sender, ws_id, path)


@router.callback_query(F.data.startswith("file:view"))
async def on_view(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    path = decoded.key[1]
    content = await services.files.view(ctx.tg_id, ws_id, path)
    await show_screen(cb, file_screens.file_view(content, ws_id, ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("file:more"))
async def on_more(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    path = decoded.key[1]
    content = await services.files.view(ctx.tg_id, ws_id, path, line_range=(100, 200))
    await show_screen(cb, file_screens.file_view(content, ws_id, ctx.locale), ctx=ctx, sender=sender)


# -- mkdir -------------------------------------------------------------------
@router.callback_query(F.data.startswith("file:mkdir"))
async def on_mkdir(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    path = decoded.key[1] if len(decoded.key) > 1 else "/"
    await state.set_state(Input.waiting_mkdir_path)
    await state.update_data(ws_id=ws_id, base_path=path)
    await show_screen(cb, file_screens.mkdir_prompt(_wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(Input.waiting_mkdir_path)
async def on_mkdir_path(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    path = (message.text or "").strip()
    await services.files.mkdir(ctx.tg_id, ws_id, path)
    listing = await services.files.list(ctx.tg_id, ws_id, data.get("base_path", "/"))
    screen = file_screens.file_browser(listing, ws_id, ctx.locale)
    await show_screen(message, screen, ctx=ctx, sender=sender, force_send=True)


# -- rename ------------------------------------------------------------------
@router.callback_query(F.data.startswith("file:rename_same"))
async def on_rename_same(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    path = decoded.key[1]
    await state.set_state(Input.waiting_rename_path)
    await state.update_data(ws_id=ws_id, src=path, same_folder=True)
    await show_screen(cb, file_screens.rename_prompt(path, _wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("file:rename"))
async def on_rename(cb: CallbackQuery, ctx: UserContext, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    path = decoded.key[1]
    await state.set_state(Input.waiting_rename_path)
    await state.update_data(ws_id=ws_id, src=path, same_folder=False)
    await show_screen(cb, file_screens.rename_prompt(path, _wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(Input.waiting_rename_path)
async def on_rename_text(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    await state.clear()
    ws_id = _wid(data["ws_id"])
    src = data["src"]
    new = (message.text or "").strip()
    if data.get("same_folder") and not new.startswith("/"):
        parent = src.rsplit("/", 1)[0]
        dst = f"{parent}/{new}" if parent else f"/{new}"
    else:
        dst = new
    await services.files.rename(ctx.tg_id, ws_id, src, dst)
    from cloudbot.presentation.rich.document import RichDoc
    from cloudbot.presentation.rich.tags import fragment

    doc = RichDoc(ctx.locale)
    doc.blockquote(fragment(catalog.t("file.rename.done", ctx.locale, path=dst)))
    await show_screen(message, _screen(doc), ctx=ctx, sender=sender, force_send=True)


# -- delete ------------------------------------------------------------------
@router.callback_query(F.data.startswith("file:delete_confirm"))
async def on_delete_confirm(cb: CallbackQuery, ctx: UserContext, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    path = decoded.key[1]
    await show_screen(cb, file_screens.delete_confirm(path, _wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.callback_query(F.data.startswith("file:delete"))
async def on_delete(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender) -> None:
    decoded = decode(cb.data)
    ws_id = _wid(decoded.key[0])
    path = decoded.key[1]
    await services.files.delete(ctx.tg_id, ws_id, path)
    parent = path.rsplit("/", 1)[0] or "/"
    listing = await services.files.list(ctx.tg_id, ws_id, parent)
    screen = file_screens.file_browser(listing, ws_id, ctx.locale)
    await show_screen(cb, screen, ctx=ctx, sender=sender)


# -- upload ------------------------------------------------------------------
@router.callback_query(F.data.startswith("file:upload"))
async def on_upload(cb: CallbackQuery, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext) -> None:
    decoded = decode(cb.data)
    ws_id = decoded.key[0]
    path = decoded.key[1] if len(decoded.key) > 1 else "/"
    meta = await services.files.meta()
    await state.set_state(Input.waiting_upload)
    await state.update_data(ws_id=ws_id, upload_path=path)
    await show_screen(cb, file_screens.upload_prompt(path, meta.upload_mb, _wid(ws_id), ctx.locale), ctx=ctx, sender=sender)


@router.message(Input.waiting_upload, F.document)
async def on_upload_document(
    message: Message, ctx: UserContext, services: Services, sender: RichSender, state: FSMContext
) -> None:
    data = await state.get_data()
    ws_id = _wid(data["ws_id"])
    path = data.get("upload_path", "/")
    document = message.document
    meta = await services.files.meta()
    limit_bytes = meta.upload_mb * 1024 * 1024
    from cloudbot.presentation.i18n.formatters import format_bytes

    if document.file_size and limit_bytes and document.file_size > limit_bytes:
        screen = file_screens.upload_too_large(
            path,
            format_bytes(document.file_size, ctx.locale),
            format_bytes(limit_bytes, ctx.locale),
            ws_id,
            ctx.locale,
        )
        await show_screen(message, screen, ctx=ctx, sender=sender, force_send=True)
        return
    target = f"{path.rstrip('/')}/{document.file_name}"
    buffer = message.bot.download(document)

    async def _stream():
        async with buffer as reader:
            while True:
                chunk = await reader.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    await services.files.upload(
        ctx.tg_id, ws_id, target, document.file_name, _stream(), size_bytes=document.file_size
    )
    await state.clear()
    listing = await services.files.list(ctx.tg_id, ws_id, path)
    screen = file_screens.file_browser(listing, ws_id, ctx.locale)
    await show_screen(message, screen, ctx=ctx, sender=sender, force_send=True)


def _screen(doc):
    from cloudbot.presentation.screens.screen import Screen

    return Screen(doc=doc)
