"""TASK python-templates — the starter catalog, the write path and the bite.

Covers the four claims the task makes testable:

1. **Internal consistency** — every template's files are runnable as written:
   every path is relative and inside the root, every non-stdlib import names a
   dependency its README mentions, every local import names a file the template
   creates, and a README exists.
2. **Exact writes** — the create flow writes exactly the declared paths and
   nothing else (measured against the paths the engine received).
3. **The bite** — a template whose file already exists refuses and writes
   **zero** files; removing the collision check makes the guard fail.
4. **Entry point** — the workspace screen carries the templates button, its
   callback is routable, and the label is unique across every screen.

The three result screens (created / refused / failed) are locked as goldens by
the catalog suite in ``tests/golden`` (the ``tpl_`` entries).
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cloudbot.adapters.cloud_service.fake import FakeEngine
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.locale import Locale
from cloudbot.domain import templates as tpl
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.callbacks import cb
from cloudbot.presentation.screens import workspaces as ws_screens
from cloudbot.services.files import FilesService, FileWrite, TemplatePathsExist
from cloudbot.services.ownership import OwnershipService

LOCALES = (Locale.EN, Locale.AR)
TG = 777

#: Packages a template may use on purpose; the README must mention each one.
KNOWN_DEPENDENCIES = {
    "flask": "flask",
    "httpx": "httpx",
    "pytest": "pytest",
}


# ---------------------------------------------------------------------------
# 1. internal consistency
# ---------------------------------------------------------------------------
def test_there_are_six_to_eight_templates():
    assert 6 <= len(tpl.TEMPLATES) <= 8
    assert len({t.key for t in tpl.TEMPLATES}) == len(tpl.TEMPLATES)


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_every_path_is_relative_and_inside_the_root(template):
    for file in template.files:
        path = file.path
        assert path, "empty path"
        assert not path.startswith("/"), f"absolute path {path!r} in {template.key}"
        parts = path.split("/")
        assert ".." not in parts, f"parent escape {path!r} in {template.key}"
        assert "" not in parts, f"empty segment in {path!r}"


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_every_template_has_a_readme(template):
    assert any(f.path == "README.md" for f in template.files), template.key


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_contents_are_ascii_code(template):
    """Code files: no Arabic and no non-ASCII (the product has no locale in code)."""
    for file in template.files:
        assert file.content.isascii(), f"non-ASCII in {template.key}:{file.path}"
        assert not any("\u0600" <= ch <= "\u06ff" for ch in file.content)


def _imports(source: str) -> list[str]:
    """The top-level module of every ``import``/``from`` in ``source``."""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module.split(".")[0])
    return names


#: Local top-level packages a template creates (the first path segment).
def _local_roots(template) -> set[str]:
    roots = set()
    for file in template.files:
        head = file.path.split("/", 1)[0]
        if head.endswith(".py"):
            roots.add(head[: -len(".py")])
        else:
            roots.add(head)
    return roots


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_every_python_file_compiles(template):
    """Runnable as written: every ``.py`` parses (py_compile, no placeholder that raises)."""
    for file in template.files:
        if file.path.endswith(".py"):
            compile(file.content, f"{template.key}:{file.path}", "exec")


#: The dependency-free templates really run (their command, run from the root).
_RUNNABLE = {
    "hello": ["hello.py"],
    "script-with-args": ["greet.py", "World", "--times", "2"],
    "cli-typer-or-argparse-app": ["cli.py", "sum", "1", "2", "3"],
    "data-csv": ["summarize.py"],
    "module": ["-m", "unittest", "discover", "-s", "tests", "-q"],
}


@pytest.mark.parametrize("key", sorted(_RUNNABLE))
def test_a_dependency_free_template_really_runs(key, tmp_path):
    """Write the declared files to a real directory and run the entry point."""
    import subprocess

    template = tpl.by_key(key)
    assert template is not None
    for file in template.files:
        target = tmp_path / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, *_RUNNABLE[key]], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, (key, result.stdout, result.stderr)


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_every_import_resolves_to_stdlib_dependency_or_created_file(template):
    local = _local_roots(template)
    created = {f.path for f in template.files}
    readme = next(f.content for f in template.files if f.path == "README.md")
    for file in template.files:
        if not file.path.endswith(".py"):
            continue
        for module in _imports(file.content):
            if module in sys.stdlib_module_names:
                continue
            if module in KNOWN_DEPENDENCIES:
                # A dependency is allowed only when the README says so.
                assert KNOWN_DEPENDENCIES[module] in readme.lower(), (
                    f"{template.key}:{file.path} imports {module} but the README "
                    f"does not mention it"
                )
                continue
            # Otherwise it must be a module the template itself creates.
            assert module in local, (
                f"{template.key}:{file.path} imports {module!r}, which is neither "
                f"stdlib, a named dependency, nor a created module {sorted(local)}"
            )
            # And the created module's file must really exist.
            assert (
                f"{module}.py" in created
                or any(p.startswith(module + "/") for p in created)
            ), f"{template.key}: {module!r} has no file"


# ---------------------------------------------------------------------------
# 2. exact writes
# ---------------------------------------------------------------------------
class _Users:
    async def owns(self, tg_id, ws_id) -> bool:  # noqa: ANN001 - test double
        return True


def _service(engine) -> FilesService:
    return FilesService(engine, OwnershipService(_Users()))


async def _clean_workspace(engine: FakeEngine):
    ws = engine.add_workspace("templates")
    return ws


async def test_the_create_flow_writes_exactly_the_declared_paths_and_nothing_else():
    engine = FakeEngine()
    service = _service(engine)

    received: list[str] = []
    original = engine.write_file

    async def spy(ws_id, path, content, *, encoding="text"):
        received.append(path)
        return await original(ws_id, path, content, encoding=encoding)

    engine.write_file = spy  # type: ignore[assignment]

    for template in tpl.TEMPLATES:
        # A fresh workspace per template: several templates ship their own
        # README.md, and installing one after another legitimately collides on
        # that path (the confirmation refuses) — this test is about what one
        # install writes, not about installing every template side by side.
        ws = await _clean_workspace(engine)
        received.clear()
        writes = [
            FileWrite(path=tpl.template_path(template, f), content=f.content)
            for f in template.files
        ]
        result = await service.create_many(TG, ws.id, writes)
        assert result.ok, (template.key, result)
        assert set(received) == set(tpl.paths(template)), template.key
        assert len(received) == len(tpl.paths(template)), "a path was written twice"


# ---------------------------------------------------------------------------
# 3. the bite — a collision refuses and writes zero files
# ---------------------------------------------------------------------------
async def test_a_collision_refuses_and_writes_zero_files():
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)

    template = tpl.by_key("hello")
    assert template is not None
    # A user's own file already occupies one of the template's paths.
    await engine.write_file(ws.id, "/hello.py", "print('mine')\n")

    received: list[str] = []
    original = engine.write_file

    async def spy(ws_id, path, content, *, encoding="text"):
        received.append(path)
        return await original(ws_id, path, content, encoding=encoding)

    engine.write_file = spy  # type: ignore[assignment]

    writes = [
        FileWrite(path=tpl.template_path(template, f), content=f.content)
        for f in template.files
    ]
    with pytest.raises(TemplatePathsExist) as exc:
        await service.create_many(TG, ws.id, writes)

    assert "/hello.py" in exc.value.paths
    assert received == [], "the refusal wrote a file"
    # The user's file is untouched.
    page = await engine.list_files(ws.id, "/")
    mine = [e for e in page.entries if e.path == "/hello.py"]
    assert mine and mine[0].size == len("print('mine')\n")


async def test_the_collision_guard_is_load_bearing():
    """Bite: remove the collision check and the same flow overwrites.

    Proves the ``existing`` probe (not some incidental engine refusal) is what
    prevents the overwrite; if the guard were deleted this test would go red,
    because the write would then land on the user's file.
    """
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)
    template = tpl.by_key("hello")
    assert template is not None
    await engine.write_file(ws.id, "/hello.py", "print('mine')\n")

    async def no_collisions(*_args, **_kwargs):
        return []

    service.existing = no_collisions  # type: ignore[assignment]

    writes = [
        FileWrite(path=tpl.template_path(template, f), content=f.content)
        for f in template.files
    ]
    result = await service.create_many(TG, ws.id, writes)
    assert result.ok and "/hello.py" in result.written  # the bite: it overwrote


async def test_a_partial_engine_refusal_is_reported_honestly():
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)
    template = tpl.by_key("module")
    assert template is not None

    from cloudbot.core.errors import EngineRefused

    original = engine.write_file
    calls = {"n": 0}

    async def fail_on_second(ws_id, path, content, *, encoding="text"):
        calls["n"] += 1
        if calls["n"] == 2:
            raise EngineRefused("insufficient_storage", "disk full")
        return await original(ws_id, path, content, encoding=encoding)

    engine.write_file = fail_on_second  # type: ignore[assignment]

    writes = [
        FileWrite(path=tpl.template_path(template, f), content=f.content)
        for f in template.files
    ]
    result = await service.create_many(TG, ws.id, writes)
    assert not result.ok
    assert result.error_code == "insufficient_storage"
    assert result.failed_path == tpl.template_path(template, template.files[1])
    assert result.written == (tpl.template_path(template, template.files[0]),)


# ---------------------------------------------------------------------------
# 4. the entry point
# ---------------------------------------------------------------------------
def _detail(status=None):
    from cloudbot.adapters.cloud_service.fake import _ws
    from cloudbot.domain.value_objects import WorkspaceStatus

    ws = _ws("ws_x", "demo", status or WorkspaceStatus.ACTIVE, active=0)
    return ws


@pytest.mark.parametrize("locale", LOCALES)
def test_the_workspace_screen_carries_the_templates_entry_point(locale):
    ws = _detail()
    screen = ws_screens.workspace_detail(ws, locale)
    flat = [b for row in screen.markup.inline_keyboard for b in row]
    entry = [b for b in flat if (b.callback_data or "").startswith("tpl:list")]
    assert entry, "the workspace screen has no templates button"
    assert entry[0].text == catalog.t("ws.templates", locale)
    assert entry[0].callback_data == cb("tpl:list", ws.id)


@pytest.mark.parametrize("locale", LOCALES)
def test_suspended_and_locked_workspaces_do_not_get_the_button(locale):
    from cloudbot.domain.value_objects import WorkspaceStatus

    for status in (WorkspaceStatus.SUSPENDED, WorkspaceStatus.LOCKED):
        ws = _detail(status)
        screen = ws_screens.workspace_detail(ws, locale)
        actions = {
            b.callback_data
            for row in screen.markup.inline_keyboard
            for b in row
            if b.callback_data
        }
        assert not any(a.startswith("tpl:") for a in actions), status


@pytest.mark.parametrize("locale", LOCALES)
def test_the_templates_label_is_unique_across_every_screen(locale):
    """The one-label-per-destination guard, applied to ``tpl:list``.

    The templates button on the workspace home and any way back to the catalog
    are both ``tpl:list``, so the *destination name* (``ws.templates``) may only
    be worn by one of them; the other is the affordance ``common.back``, which
    ``nav.NAV_LABEL_EXCEPTIONS`` already allows for a step-back.
    """
    from cloudbot.presentation.keyboards.callbacks import CallbackCodec

    CallbackCodec()
    labels: set[str] = set()
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        screen = screen_catalog.build(name, locale)
        markup = screen.markup
        if markup is None:
            continue
        for row in getattr(markup, "inline_keyboard", None) or []:
            for button in row:
                if (button.callback_data or "").startswith("tpl:list"):
                    if button.text == catalog.t("common.back", locale):
                        continue  # the affordance, not a second name
                    labels.add(button.text)
    assert labels == {catalog.t("ws.templates", locale)}, labels


@pytest.mark.parametrize("locale", LOCALES)
def test_the_failed_screen_never_prints_the_raw_engine_message(locale):
    """AGENTS.md rule 6: no raw engine English may reach a user.

    The screen takes ``engine_message`` for logging but must render only the
    catalog sentence for the code.
    """
    from cloudbot.presentation.screens import templates as tpl_screens

    template = tpl.by_key("flask-api")
    assert template is not None
    html = tpl_screens.done_failed(
        template,
        "ws_x",
        locale,
        failed_path="/README.md",
        error_code="insufficient_storage",
        written=("/app.py",),
        engine_message="internal detail a user must never see",
    ).doc.html
    assert "internal detail a user must never see" not in html
    assert catalog.t("err.insufficient_storage", locale) in html
    # Partial state is stated, not implied.
    assert "/app.py" in html


@pytest.mark.parametrize("locale", LOCALES)
def test_the_list_is_one_block_per_template_and_never_a_table(locale):
    from cloudbot.presentation.screens import templates as tpl_screens

    screen = tpl_screens.list_screen("ws_x", locale)
    html = screen.doc.html
    assert "<table" not in html, "the template list must not be a table"
    for template in tpl.TEMPLATES:
        assert catalog.t(template.title_key, locale) in html
        assert catalog.t(template.detail_key, locale) in html
    # The header is the house shape: ✦ label ⟸ count.
    assert "⟸" in html


@pytest.mark.parametrize("locale", LOCALES)
def test_the_workspace_entry_is_its_own_full_width_row(locale):
    ws = _detail()
    screen = ws_screens.workspace_detail(ws, locale)
    rows = [
        row
        for row in screen.markup.inline_keyboard
        if any((b.callback_data or "").startswith("tpl:list") for b in row)
    ]
    assert len(rows) == 1 and len(rows[0]) == 1, rows


# ---------------------------------------------------------------------------
# end to end through the real dispatcher
# ---------------------------------------------------------------------------
class FakeBot:
    id = 42

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_rich_message(
        self, chat_id, rich_message, reply_markup=None, reply_parameters=None
    ):
        self.sent.append(rich_message.html or "")
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(
        self, chat_id, message_id, rich_message=None, reply_markup=None
    ):
        self.sent.append(getattr(rich_message, "html", "") or "")
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def __call__(self, method):
        return True


def _callback(uid: int, user_id: int, data: str) -> Update:
    msg = Message(
        message_id=uid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text="screen",
    )
    return Update(
        update_id=uid,
        callback_query=CallbackQuery(
            id=str(uid),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="c",
            message=msg,
            data=data,
        ),
    )


def _settings() -> Settings:
    return Settings(
        mode="dev",
        bot_token="1:TEST",
        engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:",
        mock_engine=True,
        admin_ids={TG},
        _env_file=None,
    )


async def _harness():
    container = build(_settings())
    await container.database.migrate()
    ws = await container.services.workspaces.create(TG, "mine", plan_code="trial")
    bot = FakeBot()
    dispatcher = build_dispatcher(container, bot)
    return container, ws, bot, dispatcher


async def test_the_flow_creates_the_declared_files_end_to_end():
    container, ws, bot, dispatcher = await _harness()
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("tpl:list", ws.id)))
        assert catalog.t("tpl.list.title", Locale.EN) in bot.sent[-1]

        await dispatcher.feed_update(bot, _callback(2, TG, cb("tpl:pick", "hello", ws.id)))
        assert "/hello.py" in bot.sent[-1]
        # Nothing written yet — pick only confirms.
        assert bot.sent and "/hello.py" in bot.sent[-1]

        await dispatcher.feed_update(bot, _callback(3, TG, cb("tpl:confirm", "hello", ws.id)))
        template = tpl.by_key("hello")
        assert template is not None
        page = await container.engine.list_files(ws.id, "/")
        landed = {e.path for e in page.entries}
        assert landed == set(tpl.paths(template)), landed
        assert catalog.t("tpl.done.created_title", Locale.EN) in bot.sent[-1]
    finally:
        await container.aclose()


async def test_a_template_never_overwrites_an_existing_file_end_to_end():
    container, ws, bot, dispatcher = await _harness()
    try:
        await container.engine.write_file(ws.id, "/hello.py", "print('mine')\n")

        # The confirmation already flags the collision (the create button is muted).
        await dispatcher.feed_update(bot, _callback(1, TG, cb("tpl:pick", "hello", ws.id)))
        assert catalog.t("tpl.confirm.nothing_written", Locale.EN) in bot.sent[-1]

        # Confirming anyway refuses and writes nothing.
        await dispatcher.feed_update(bot, _callback(2, TG, cb("tpl:confirm", "hello", ws.id)))
        assert catalog.t("tpl.done.refused_title", Locale.EN) in bot.sent[-1]
        page = await container.engine.list_files(ws.id, "/")
        hello = [e for e in page.entries if e.path == "/hello.py"]
        assert hello and hello[0].size == len("print('mine')\n")
        assert not any(e.path == "/README.md" for e in page.entries)
    finally:
        await container.aclose()
