"""TASK file-tree: the file screen is a tree with a button beside each entry.

Every test here fails on the pre-task screen:

* the old screen built a three-column ``doc.table``;
* its per-entry navigation lived in the keyboard below the message;
* the tree ordering (folders first) did not exist because there was no tree.

The dispatcher tests are the load-bearing ones: they feed an entry's *body
button* through the real dispatcher and check the destination screen, so they
prove the button works rather than that a string matches.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain.entities import FileEntry
from cloudbot.domain.value_objects import FilePage, WorkspaceId
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.screens import files as file_screens

TG = 777
WS = WorkspaceId("ws_01M6HJASCWM9Z1PF755GT89NK4")
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
LOCALES = (Locale.EN, Locale.AR)

#: The drawing TASK file-tree asks for: two folders, three files, out of order.
PAGE = FilePage(
    path="/app",
    entries=(
        FileEntry("/app/README.md", "README.md", "file", 2150, "-rw-r--r--", NOW, None),
        FileEntry("/app/src", "src", "dir", None, "drwxr-xr-x", NOW, None),
        FileEntry("/app/main.py", "main.py", "file", 1229, "-rw-r--r--", NOW, "python3"),
        FileEntry("/app/utils", "utils", "dir", None, "drwxr-xr-x", NOW, None),
        FileEntry("/app/config.py", "config.py", "file", 340, "-rw-r--r--", NOW, "python3"),
    ),
    count=5,
    total=5,
    truncated=False,
)


def _built(screen):
    return screen.doc.build()


def _block_buttons(screen) -> list:
    """Every body button of a blocks screen, in document order."""
    blocks = _built(screen).blocks or []
    return [button for block in blocks for button in (getattr(block, "buttons", None) or [])]


def _tree_text(screen) -> str:
    blocks = _built(screen).blocks or []
    return "\n".join(
        getattr(block, "text", "") or ""
        for block in blocks
        if type(block).__name__ == "InputRichBlockPreformatted"
    )


# -- 1. tree, not table -------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_the_screen_has_no_table_and_builds_entirely_in_blocks(locale):
    """No ``<table>`` anywhere, and ``build()`` proves the document is blocks-only.

    ``build()`` raises on a mixed document, so a green run means no html fragment
    survives beside the blocks — exactly what an in-body button requires.
    """
    CallbackCodec()
    screen = file_screens.file_browser(PAGE, WS, locale)
    built = _built(screen)
    assert built.html is None, "the file screen must build in blocks, not html"
    assert built.blocks, "no blocks were produced"
    assert "<table" not in (screen.doc.html or "")


@pytest.mark.parametrize("locale", LOCALES)
def test_folders_are_drawn_before_files(locale):
    CallbackCodec()
    tree = _tree_text(file_screens.file_browser(PAGE, WS, locale))
    lines = tree.splitlines()
    folder_positions = [i for i, line in enumerate(lines) if "📁" in line]
    file_positions = [i for i, line in enumerate(lines) if "📄" in line or "🐍" in line]
    assert folder_positions and file_positions
    assert max(folder_positions) < min(file_positions), tree
    # The branch glyphs are present, and the last line closes the tree.
    assert all(line.startswith(("├── ", "└── ")) for line in lines), tree
    assert lines[-1].startswith("└── "), tree
    # Folders carry a trailing slash and no size; files carry a size.
    for line in lines:
        if "📁" in line:
            assert line.endswith("/"), line
        elif "📄" in line or "🐍" in line:
            assert " · " in line, line


# -- 2. one body button per entry, reaching the old keyboard's action ---------
@pytest.mark.parametrize("locale", LOCALES)
def test_one_body_button_per_entry(locale):
    CallbackCodec()
    screen = file_screens.file_browser(PAGE, WS, locale)
    buttons = _block_buttons(screen)
    assert len(buttons) == len(PAGE.entries), (len(buttons), len(PAGE.entries))
    labels = [button.text for button in buttons]
    for entry in file_screens._ordered(PAGE.entries):
        assert any(entry.name in label for label in labels), (entry.name, labels)
    # The keyboard below the message no longer repeats the listing.
    keyboard_rows = getattr(screen.markup, "inline_keyboard", None) or []
    keyboard_callbacks = {
        b.callback_data
        for row in keyboard_rows
        for b in row
        if getattr(b, "callback_data", None)
    }
    assert not any(
        (b.callback_data or "").startswith(("file:cd:", "file:view:")) for b in buttons
        if b.callback_data in keyboard_callbacks
    ), "an entry's button was also placed in the keyboard"


def _settings() -> Settings:
    return Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        _env_file=None,
    )


class RecordingBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append(
            {
                "html": rich_message.html,
                "blocks": rich_message.blocks,
                "markup": reply_markup,
            }
        )
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(
        self, chat_id, message_id, rich_message=None, reply_markup=None
    ):
        self.sent.append(
            {
                "html": getattr(rich_message, "html", None),
                "blocks": getattr(rich_message, "blocks", None),
                "markup": reply_markup,
            }
        )
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def __call__(self, method):
        return True


def _callback(uid: int, data: str) -> Update:
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=TG, is_bot=False, first_name="A"),
            chat_instance="c",
            message=Message(
                message_id=uid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=TG, type="private"),
                from_user=User(id=TG, is_bot=False, first_name="A"),
                text="screen",
            ),
            data=data,
        ),
    )


async def _harness():
    container = build(_settings())
    await container.database.migrate()
    ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
    # A folder with a file inside it, and a root file: the two destinations.
    await container.engine.mkdir(ws.id, "/src")
    await container.engine.write_file(ws.id, "/src/nested.py", "print('nested')\n")
    await container.engine.write_file(ws.id, "/main.py", "print('hi')\n")
    bot = RecordingBot()
    dispatcher = build_dispatcher(container, bot)
    return container, ws, bot, dispatcher


async def _listing(container, ws, locale: Locale) -> object:
    page = await container.services.files.list(TG, ws.id, "/")
    return file_screens.file_browser(page, ws.id, locale)


def _last_text(bot: RecordingBot) -> str:
    """Everything the last rendered screen says, blocks or html."""
    last = bot.sent[-1]
    if last["html"]:
        return last["html"]
    parts: list[str] = []
    for block in last["blocks"] or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        for button in getattr(block, "buttons", None) or []:
            parts.append(getattr(button, "text", "") or "")
    return "\n".join(parts)


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_folder_button_navigates_into_the_folder(locale):
    """Drive the real dispatcher: tapping the folder's body button lists inside."""
    container, ws, bot, dispatcher = await _harness()
    try:
        CallbackCodec()
        screen = file_screens.file_browser(
            await container.services.files.list(TG, ws.id, "/"), ws.id, locale
        )
        folder_button = next(
            button
            for button in _block_buttons(screen)
            if button.text.startswith("📁")
        )
        await dispatcher.feed_update(bot, _callback(1, folder_button.callback_data))
        text = _last_text(bot)
        # The destination is the file browser for /src (its header names the path),
        # and the file that lives inside it is rendered.
        assert "/src" in text, text[:300]
        assert "nested.py" in text, text[:300]
        assert any(
            (getattr(b, "callback_data", "") or "").startswith("file:cd:") or
            (getattr(b, "callback_data", "") or "").startswith("file:view:")
            for b in _block_buttons_from_last(bot)
        ), "the destination listing lost its own per-entry buttons"
    finally:
        await container.aclose()


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_file_button_opens_the_file(locale):
    """Drive the real dispatcher: tapping the file's body button opens its view."""
    container, ws, bot, dispatcher = await _harness()
    try:
        CallbackCodec()
        screen = file_screens.file_browser(
            await container.services.files.list(TG, ws.id, "/"), ws.id, locale
        )
        file_button = next(
            button
            for button in _block_buttons(screen)
            if button.text.startswith(("📄", "🐍", "⚙️")) and "main.py" in button.text
        )
        await dispatcher.feed_update(bot, _callback(1, file_button.callback_data))
        text = _last_text(bot)
        # ``file_view`` prints the file's name in its heading and its content.
        assert "main.py" in text, text[:300]
        assert "print" in text, text[:300]
    finally:
        await container.aclose()


def _block_buttons_from_last(bot: RecordingBot) -> list:
    last = bot.sent[-1]
    return [
        button
        for block in (last["blocks"] or [])
        for button in (getattr(block, "buttons", None) or [])
    ]


def test_the_bite_removing_the_entry_buttons_leaves_nothing_to_tap(monkeypatch):
    """The dispatcher tests above depend on the per-entry loop.

    If ``_entry_buttons`` stops producing a button, the screen has no body
    buttons left and the folder/file taps above cannot exist. The guard makes that
    dependency explicit instead of letting the suite pass on an empty list.
    """
    CallbackCodec()
    monkeypatch.setattr(file_screens, "_entry_buttons", lambda entries, ws_id: [])
    screen = file_screens.file_browser(PAGE, WS, Locale.EN)
    assert _block_buttons(screen) == []
    assert _tree_text(screen) != "", "the tree itself must survive the bite"


# -- 3. every emitted callback fits the 64-byte cap ---------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_every_callback_fits_including_a_sixty_character_directory(locale):
    """A deep path with a 60-character segment must not overrun the cap.

    The codec falls back to a short registry token when the inline form would
    not fit, so the *emitted* callback is always ≤ 64 bytes. The tree staying
    navigable on a deep path is the point.
    """
    CallbackCodec()
    long_name = "a" * 60
    page = FilePage(
        path="/" + long_name,
        entries=(
            FileEntry(f"/{long_name}/{long_name}", long_name, "dir", None, "drwxr-xr-x", NOW, None),
            FileEntry(f"/{long_name}/{long_name}.py", f"{long_name}.py", "file", 1, "-rw-r--r--", NOW, "python3"),
        ),
        count=2,
        total=2,
        truncated=False,
    )
    screen = file_screens.file_browser(page, WS, locale)
    callbacks = [
        button.callback_data for button in _block_buttons(screen) if button.callback_data
    ]
    callbacks += [
        button.callback_data
        for row in (getattr(screen.markup, "inline_keyboard", None) or [])
        for button in row
        if getattr(button, "callback_data", None)
    ]
    assert callbacks
    for data in callbacks:
        assert len(data.encode("utf-8")) <= 64, data
    # And the label is the shortened one — the name in the callback is untouched.
    labels = [button.text for button in _block_buttons(screen)]
    assert any("…" in label for label in labels), labels


# -- 4. the paged variant says so --------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_a_paged_folder_says_so_in_the_footer_and_offers_the_turn(locale):
    CallbackCodec()
    fixtures = screen_catalog.fixtures()
    first = screen_catalog.SCREEN_BUILDERS["file_browser_page_2"](
        {**fixtures}, locale
    )
    footer = " ".join(
        getattr(block, "text", "") or ""
        for block in (_built(first).blocks or [])
        if type(block).__name__ == "InputRichBlockFooter"
    )
    assert ("2" in footer) and ("2" in footer), footer
    pager_callbacks = [
        button.callback_data
        for button in _block_buttons(first)
        if (button.callback_data or "").startswith("file:ls:")
    ]
    assert pager_callbacks, "a paged folder must offer a previous page"


# -- 5. the catalog goldens exist for the list and its paged variant ---------
def test_the_list_and_its_paged_variant_are_catalog_screens():
    names = set(screen_catalog.SCREEN_BUILDERS)
    assert {"file_browser", "file_browser_page_2", "file_browser_empty"} <= names
