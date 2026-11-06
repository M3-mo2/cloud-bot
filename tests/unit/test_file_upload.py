"""D1 — the document upload path, driven through the real dispatcher.

The live defect: ``on_upload_document`` called ``message.bot.download(document)``
**without ``await``**. ``Bot.download`` is a coroutine (aiogram 3.31.0) that returns
a plain ``BinaryIO`` (a ``BytesIO`` when ``destination`` is None); the old code then
used it with ``async with``, so the handler raised
``TypeError: 'coroutine' object does not support the asynchronous context manager
protocol`` (plus ``RuntimeWarning: coroutine 'Bot.download' was never awaited``)
and the user saw nothing at all. It survived 1001 tests because **no test drove
``on_upload_document``** — there was no ``FakeBot.download`` anywhere.

These tests add the missing double (a ``download`` that mirrors aiogram's real
behaviour: a *coroutine* returning a ``BinaryIO``) and pin the path end to end:
the engine receives the file's exact bytes, and the refreshed listing comes back.
"""

from __future__ import annotations

import inspect
import io
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Document, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.states.input import Input

TG = 777

CONTENT = b"""print("hello from the uploaded file")\n# uploaded by task 22\n"""


class FakeBot:
    """A bot double whose ``download`` mirrors the real aiogram coroutine shape."""

    id = 42

    def __init__(self, blob: bytes = CONTENT) -> None:
        self.sent: list[dict] = []
        self._blob = blob
        self.download_calls: list[Document] = []

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append({"html": rich_message.html, "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def download(self, file, destination=None, **kwargs):
        """A coroutine returning a plain ``BinaryIO`` — exactly aiogram's contract."""
        self.download_calls.append(file)
        buffer = io.BytesIO(self._blob)
        if destination is not None:  # pragma: no cover - the handler passes None
            destination.write(self._blob)
            destination.seek(0)
            return destination
        return buffer

    async def __call__(self, method):
        return True


def _document() -> Document:
    return Document(
        file_id="file-id",
        file_unique_id="unique",
        file_name="server.py",
        file_size=len(CONTENT),
    )


def _document_message(uid: int, user_id: int, document: Document) -> Update:
    return Update(
        update_id=uid,
        message=Message(
            message_id=uid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            document=document,
        ),
    )


async def _harness(blob: bytes = CONTENT):
    settings = Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )
    container = build(settings)
    assert isinstance(container.engine, FakeEngine)
    await container.database.migrate()
    ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")

    received: list[bytes] = []
    engine = container.engine
    real_upload = engine.upload_file

    async def spy_upload(ws_id, path, filename, stream):
        chunks = [chunk async for chunk in stream]
        received.append(b"".join(chunks))
        return await real_upload(ws_id, path, filename, _replay(chunks))

    engine.upload_file = spy_upload  # type: ignore[assignment]

    bot = FakeBot(blob)
    dispatcher = build_dispatcher(container, bot)
    return container, ws, bot, dispatcher, received


async def _replay(chunks):
    for chunk in chunks:
        yield chunk


async def test_a_document_driven_through_the_dispatcher_reaches_the_engine():
    container, ws, bot, dispatcher, received = await _harness()
    try:
        # The user tapped *Upload a file*: enter the waiting state for this folder.
        await dispatcher.fsm.storage.set_state(
            _key(bot, TG), Input.waiting_upload.state
        )
        await dispatcher.fsm.storage.set_data(
            _key(bot, TG), {"ws_id": str(ws.id), "upload_path": "/dest"}
        )
        await dispatcher.feed_update(bot, _document_message(1, TG, _document()))

        # The engine received the exact bytes — content, not "a call happened".
        assert received == [CONTENT], "the engine did not receive the file's bytes"
        # And the refreshed listing screen came back (the last thing sent).
        assert bot.sent, "nothing rendered after the upload"
        last = bot.sent[-1]["html"]
        assert "📁 Files" in last or "📁 الملفات" in last, last[:200]
        # The state was cleared, as the handler promises.
        assert await dispatcher.fsm.storage.get_state(_key(bot, TG)) is None
    finally:
        await container.aclose()


async def test_the_upload_uses_a_plain_binary_io_not_an_async_context():
    """The exact shape that broke: the awaited ``download`` is a sync ``BinaryIO``."""
    container, ws, bot, dispatcher, received = await _harness()
    try:
        await dispatcher.fsm.storage.set_state(_key(bot, TG), Input.waiting_upload.state)
        await dispatcher.fsm.storage.set_data(
            _key(bot, TG), {"ws_id": str(ws.id), "upload_path": "/"}
        )
        await dispatcher.feed_update(bot, _document_message(1, TG, _document()))
        assert bot.download_calls, "Bot.download was never awaited"
        assert received == [CONTENT]
    finally:
        await container.aclose()


async def test_an_over_cap_document_is_refused_before_uploading():
    """The size guard still fires first (no engine round trip for a too-large file)."""
    container, ws, bot, dispatcher, received = await _harness()
    try:
        meta = await container.services.files.meta()
        too_big = (meta.upload_mb + 1) * 1024 * 1024
        await dispatcher.fsm.storage.set_state(_key(bot, TG), Input.waiting_upload.state)
        await dispatcher.fsm.storage.set_data(
            _key(bot, TG), {"ws_id": str(ws.id), "upload_path": "/"}
        )
        oversized = Document(
            file_id="big", file_unique_id="big-u", file_name="big.bin", file_size=too_big
        )
        await dispatcher.feed_update(bot, _document_message(1, TG, oversized))
        assert received == [], "an over-cap file was uploaded anyway"
        assert bot.download_calls == [], "an over-cap file was downloaded anyway"
        assert bot.sent, "the too-large screen did not render"
    finally:
        await container.aclose()


def test_bot_download_is_a_coroutine_returning_a_binary_io():
    """Bind the handler to the real seam: ``Bot.download`` is a coroutine function."""
    assert inspect.iscoroutinefunction(Bot.download)
    hint = inspect.signature(Bot.download).return_annotation
    assert "BinaryIO" in str(hint), hint


async def test_the_handler_awaits_download_and_reads_it_synchronously():
    """A contract check on the exact wrong shape: the handler must ``await``
    ``download`` and must not use it as an async context manager."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "cloudbot" / "handlers" / "files.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    source = path.read_text(encoding="utf-8")

    awaited_download = any(
        isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "attr", None) == "download"
        for node in ast.walk(tree)
    )
    assert awaited_download, "on_upload_document must await message.bot.download(...)"
    assert "async with buffer" not in source, "a BinaryIO is not an async context manager"
    assert "async with buffer as reader" not in source


def _key(bot, user_id):
    return StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
