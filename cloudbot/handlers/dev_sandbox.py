"""The developer-tools sandbox: a real dispatcher with nothing real behind it.

The ``/dev`` button inspector must drive the **real dispatcher** (so a "tap" shows
the real next screen, not a guess) while guaranteeing three hard requirements:

1. **Nothing reaches Telegram.** Every handler response goes to a ``FakeBot``
   double (the same one the contract tests use), never the live bot. The handler
   renders only its own summary screens through the real sender.
2. **Nothing reaches the engine.** The sandbox builds a **separate**, in-memory
   container forced to the ``FakeEngine`` and a ``:memory:`` database, whatever
   the production settings say. No HTTP client is ever constructed.
3. **Read-only.** Because the sandbox's state is its own (in-memory DB + fake
   engine), the tool cannot touch the live bot's database or the engine by
   construction.

Building a second dispatcher re-attaches the process-global handler routers, so
the modules are detached first (exactly as ``tests/conftest.py`` does). The real
dispatcher keeps its own ``sub_routers`` references and its update-level
middleware (the admin gate), so it is unaffected — verified by the architecture
tests. The tool is off by default (``CLOUDBOT_DEV_TOOLS``), so this heavier seam
is only ever taken in development.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.domain.value_objects import WorkspaceId, WorkspaceStatus
from cloudbot.presentation.keyboards.callbacks import CallbackCodec

#: The fixture workspace ids the catalog renders; the sandbox owns each of them so
#: no scoped handler stops at the isolation gate.
FIXTURE_WS = (
    "ws_01M6HJASCWM9Z1PF755GT89NK4",
    "ws_01M6HJASCWM9Z1PF755GT89NK5",
    "ws_01M6HJASCWM9Z1PF755GT89NK6",
)

#: The one double every caller shares (the contract tests import this too).
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append({"html": getattr(rich_message, "html", ""), "markup": reply_markup})
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_document(self, chat_id, document, reply_parameters=None):
        return Message.model_construct(chat={"id": chat_id}, message_id=1)

    async def download(self, document):
        return io.BytesIO(b"data")

    async def __call__(self, method):
        return True


#: The handler modules whose process-global routers ``build_dispatcher`` attaches.
def _handler_modules():
    from cloudbot.handlers import (
        admin,
        admin_panel,
        billing,
        callbacks,
        common,
        execution,
        features,
        files,
        terminal,
        workspaces,
    )

    return (
        common,
        billing,
        callbacks.navigation,
        admin_panel,
        workspaces,
        files,
        execution,
        features,
        admin,
        terminal,
    )


def _detach_routers() -> None:
    """Detach the global routers so a fresh dispatcher can attach them again."""
    for module in _handler_modules():
        module.router._parent_router = None


def _callback_handlers(router) -> list:
    handlers: list = []
    observer = router.observers.get("callback_query")
    if observer is not None:
        handlers.extend(observer.handlers)
    for sub in getattr(router, "sub_routers", []):
        handlers.extend(_callback_handlers(sub))
    return handlers


class DevSandbox:
    """A real dispatcher over an isolated, in-memory, fake-engine world."""

    def __init__(self, container, dispatcher, bot: FakeBot, tg_id: int, locale: Locale) -> None:
        self.container = container
        self.dispatcher = dispatcher
        self.bot = bot
        self.tg_id = tg_id
        self.locale = locale
        self._handlers = _callback_handlers(dispatcher)

    @classmethod
    async def start(
        cls, settings: Settings, tg_id: int, locale: Locale, bot: FakeBot | None = None
    ) -> "DevSandbox":
        sandbox_settings = settings.model_copy(
            update={
                # Force the fake engine and an isolated, in-memory DB, whatever the
                # production settings say. DEV makes ``mock_engine_allowed`` true.
                "mode": RunMode.DEV,
                "mock_engine": True,
                "database_url": "sqlite+aiosqlite:///:memory:",
                "admin_ids": frozenset({tg_id}),
                # The sandbox never nests the tool itself.
                "dev_tools": False,
            }
        )
        container = build(sandbox_settings)
        await container.database.migrate()
        await container.users.get_or_create(tg_id, default_locale=locale)
        for ws_id in FIXTURE_WS:
            await container.users.add_workspace(tg_id, WorkspaceId(ws_id))
        for ws_id, name, status in (
            ("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE),
            ("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED),
            ("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED),
        ):
            container.engine._seed_workspace(ws_id, name, status)
        await container.services.workspaces.create(tg_id, "mine", plan_code="trial")
        _detach_routers()
        bot = bot if bot is not None else FakeBot()
        dispatcher = build_dispatcher(container, bot)
        return cls(container, dispatcher, bot, tg_id, locale)

    async def aclose(self) -> None:
        await self.container.aclose()

    async def reset(self) -> None:
        """Restore the fake world between taps/walks (destructive buttons mutate it)."""
        for ws_id, name, status in (
            ("ws_01M6HJASCWM9Z1PF755GT89NK4", "demo", WorkspaceStatus.ACTIVE),
            ("ws_01M6HJASCWM9Z1PF755GT89NK5", "scraper", WorkspaceStatus.SUSPENDED),
            ("ws_01M6HJASCWM9Z1PF755GT89NK6", "legacy", WorkspaceStatus.LOCKED),
        ):
            self.container.engine._seed_workspace(ws_id, name, status)
        for ws_id in FIXTURE_WS:
            await self.container.users.add_workspace(self.tg_id, WorkspaceId(ws_id))
        # Go through the service seam (never the repository's session accessor).
        await self.container.services.session.leave(self.tg_id)

    def _update(self, uid: int, data: str) -> Update:
        return Update(
            update_id=uid,
            callback_query=CallbackQuery(
                id=str(uid),
                from_user=User(id=self.tg_id, is_bot=False, first_name="A"),
                chat_instance="c",
                message=Message(
                    message_id=uid,
                    date=datetime.now(timezone.utc),
                    chat=Chat(id=self.tg_id, type="private"),
                    from_user=User(id=self.tg_id, is_bot=False, first_name="A"),
                    text="screen",
                ),
                data=data,
            ),
        )

    async def claims(self, data: str) -> bool:
        """True when some handler's own filters accept ``data`` (the real routing)."""
        event = self._update(0, data).callback_query
        for handler in self._handlers:
            try:
                passed, _ = await handler.check(event)
            except Exception:  # noqa: BLE001 - a raising filter is a non-match
                continue
            if passed:
                return True
        return False

    async def tap(self, data: str, uid: int = 1) -> tuple[bool, str | None, str | None]:
        """Feed one callback through the real dispatcher via the fake bot.

        Returns ``(rendered, html, error)``: whether a new screen was produced, the
        rendered html (the last message), and an exception summary if the tap
        raised. The tool is safe when a screen is broken: the exception is reported,
        not propagated.
        """
        before = len(self.bot.sent)
        try:
            await self.dispatcher.feed_update(self.bot, self._update(uid, data))
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            return False, None, f"{type(exc).__name__}: {exc}"
        if len(self.bot.sent) <= before:
            return False, None, None
        return True, self.bot.sent[-1]["html"], None


def reset_tokens() -> None:
    """Reset the global callback token registry (a simulated restart)."""
    from cloudbot.presentation.keyboards.callbacks import codec

    codec.reset()


# Re-export for callers that want a deterministic codec per render.
__all__ = ["FakeBot", "DevSandbox", "FIXTURE_WS", "reset_tokens", "CallbackCodec"]
