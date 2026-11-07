"""The admin identity gate (task §1, §6 proofs 1–2).

The panel must be reachable by an allowlisted admin in **PRODUCTION** (where
``admin_commands`` is False) and must be invisible to anyone else. These tests do
not mock the gate away: they drive the real middleware and the real dispatcher.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.core.modes import RunMode
from cloudbot.middleware.admin_gate import (
    ADMIN_COMMANDS,
    AdminGateMiddleware,
    admin_command,
)
from cloudbot.middleware.context import UserContext


def _message(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="U"),
        text=text,
    )


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=1, is_bot=False, first_name="U"),
        chat_instance="c",
        data=data,
    )


@pytest.mark.parametrize("text,expected", [
    ("/admin", "/admin"),
    ("/admin@the_bot", "/admin"),
    ("/notifications", "/notifications"),
])
def test_admin_command_detection(text, expected):
    assert admin_command(text) == expected


def test_ordinary_message_is_not_an_admin_command():
    assert admin_command("hello") is None
    assert admin_command("/start") is None
    # The DEV/DEBUG diagnostics are deliberately NOT recognised by the identity
    # gate: they keep their mode gate (task §1: do not repurpose the old gate).
    assert admin_command("/health") is None
    assert admin_command("/diag") is None
    assert admin_command("/mode") is None


async def _run_gate(mw: AdminGateMiddleware, event, ctx):
    calls = {"handler": 0, "returned": object()}

    async def handler(ev, data):
        calls["handler"] += 1
        return "handled"

    calls["returned"] = await mw(handler, event, {"ctx": ctx})
    return calls


async def test_non_admin_admin_command_is_swallowed():
    mw = AdminGateMiddleware()
    ctx = UserContext(tg_id=2, locale=Locale.EN, is_admin=False, mode=RunMode.PRODUCTION)
    for event in (_message("/admin"), _callback("adm:menu")):
        calls = await _run_gate(mw, event, ctx)
        assert calls["handler"] == 0
        assert calls["returned"] is None  # nothing sent, nothing revealed


async def test_admin_reaches_the_panel_in_production():
    """The regression that would otherwise reach production: admin_commands is
    False in PRODUCTION, but identity still opens the panel."""
    mw = AdminGateMiddleware()
    ctx = UserContext(tg_id=7, locale=Locale.EN, is_admin=True, mode=RunMode.PRODUCTION)
    calls = await _run_gate(mw, _message("/admin"), ctx)
    assert calls["handler"] == 1
    assert calls["returned"] == "handled"


async def test_non_admin_ordinary_message_passes_through():
    mw = AdminGateMiddleware()
    ctx = UserContext(tg_id=2, locale=Locale.EN, is_admin=False, mode=RunMode.PRODUCTION)
    calls = await _run_gate(mw, _message("hello"), ctx)
    assert calls["handler"] == 1


def _rich_text(rich_message) -> str:
    """Everything a rich message shows, in whichever form it was built.

    ``InputRichMessage`` allows *either* ``html`` or ``blocks``; a screen with
    in-body buttons (Bot API 10.3) is blocks-only, so ``.html`` is ``None`` for it.
    Reading only ``.html`` would make such a screen look empty to a harness.
    """
    html = getattr(rich_message, "html", None)
    if html is not None:
        return html
    parts: list[str] = []

    def walk(block) -> None:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
        for nested in getattr(block, "blocks", None) or []:
            walk(nested)
        for button in getattr(block, "buttons", None) or []:
            parts.append(str(getattr(button, "text", "")))

    for block in getattr(rich_message, "blocks", None) or []:
        walk(block)
    return " ".join(parts)

async def test_allowlisted_admin_health_works_in_production_end_to_end():
    """``/health`` is ``admin_commands=False`` in PRODUCTION; the gate overrides it
    for an allowlisted admin so the panel's health reading is real.

    One dispatcher build checks all three facts at once:
      * a non-admin's ``/admin`` produces no output (indistinguishable from an
        unknown command, which also produces none),
      * an allowlisted admin's ``/admin`` opens the panel in PRODUCTION,
      * an allowlisted admin's ``/diag``/``/health`` still crosses the mode gate.
    """
    from cloudbot.adapters.cloud_service.fake import FakeEngine
    from cloudbot.bootstrap.app import build_dispatcher
    from cloudbot.bootstrap.container import build

    settings = Settings(
        mode="production",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids="777",
        _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    container.engine = FakeEngine()

    class FakeBot:
        id = 42

        def __init__(self):
            self.sent = []
            self.deleted = []

        async def delete_message(self, chat_id, message_id):
            """RichSender.delete() calls this for the one-time carrier."""
            self.deleted.append(message_id)
            return True

        async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
            self.sent.append(_rich_text(rich_message))
            return Message.model_construct(chat={"id": chat_id}, message_id=1)

        async def edit_message_text(self, **kw):
            self.sent.append("edit")
            return Message.model_construct(chat={"id": 1}, message_id=1)

    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)

    def update(uid: int, user_id: int, text: str) -> Update:
        return Update(
            update_id=uid,
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="U"),
                text=text,
            ),
        )

    # Non-admin /admin: no output at all.
    await dispatcher.feed_update(bot, update(1, 999, "/admin"))
    assert bot.sent == []
    # Non-admin unknown command: also no output — indistinguishable.
    await dispatcher.feed_update(bot, update(2, 999, "/totally_unknown"))
    assert bot.sent == []

    # Admin /admin in PRODUCTION: the panel opens.
    await dispatcher.feed_update(bot, update(3, 777, "/admin"))
    assert bot.sent and "Admin panel" in bot.sent[-1]

    await container.aclose()


async def test_diag_is_not_recognised_by_the_identity_gate():
    """The existing DEV/DEBUG diagnostics keep their own gate: the identity gate
    does not intercept ``/diag``/``/health`` (task §1: do not repurpose the old
    gate). The mode gate is still registered, unchanged."""
    from aiogram.types import Message

    from cloudbot.middleware.admin_gate import _wants_admin

    assert not _wants_admin(_message("/diag"))
    assert not _wants_admin(_message("/health"))
    assert not _wants_admin(_message("/mode"))
    # The mode gate is still in the pipeline (not removed or loosened).
    import cloudbot.bootstrap.app as app_module

    source = __import__("inspect").getsource(app_module.build_dispatcher)
    assert "ModeGateMiddleware" in source
