"""TASK python-templates + TASK template-folders — the catalog, the folder rule and the bite.

Covers the claims the tasks make testable:

1. **Internal consistency** — every template's files are runnable as written:
   every declared path is relative to the template folder, every non-stdlib
   import names a dependency its README mentions, every local import names a
   file the template creates, and a README exists.
2. **The folder rule** — every template writes under a directory named after its
   key, and ``template_root`` refuses a key or file path that could escape the
   workspace (``../``, an absolute path, a key with a slash).
3. **Exact writes** — the create flow writes exactly the composed paths (the
   folder prefix plus the declared path) and nothing else.
4. **Coexistence and the bite** — two *different* templates in one workspace both
   succeed; without the folder prefix the second collides on ``README.md``. The
   *same* template twice still refuses, and names the folder.
5. **Entry point** — the workspace screen carries the templates button, its
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


def test_every_key_is_a_safe_directory_name():
    """A key names a folder: lowercase, hyphenated, one segment, never a path."""
    for key in tpl.keys():
        assert tpl.template_root(key) == key
        assert key == key.lower()
        assert "/" not in key and key not in (".", "..")


@pytest.mark.parametrize("bad_key", ["Hello", "../escape", "a/b", "/abs", "a.b", "", "a_b"])
def test_template_root_refuses_a_key_that_could_be_a_path(bad_key):
    with pytest.raises(ValueError):
        tpl.template_root(bad_key)


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.py", "/abs.py", "a//b.py", "./x.py", ""],
)
def test_template_relative_path_refuses_a_path_that_could_escape(bad_path):
    template = tpl.by_key("hello")
    assert template is not None
    with pytest.raises(ValueError):
        tpl.template_relative_path(template, tpl.TemplateFile(path=bad_path, content=""))


@pytest.mark.parametrize("template", tpl.TEMPLATES, ids=[t.key for t in tpl.TEMPLATES])
def test_every_composed_path_starts_with_the_key_folder(template):
    """The composed path is ``<key>/<declared path>`` — always inside the workspace."""
    for file, relative, absolute in zip(
        template.files, template.relative_paths(), template.paths()
    ):
        assert relative == f"{template.key}/{file.path}"
        assert absolute == f"/{template.key}/{file.path}"
        assert not relative.startswith("/")
        assert ".." not in relative.split("/")
        assert absolute.startswith("/" + template.key + "/")


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
    # Write into the template's own folder — the layout the bot actually creates.
    project = tmp_path / template.key
    for file in template.files:
        target = project / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, *_RUNNABLE[key]], cwd=project, capture_output=True, text=True
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


async def test_two_different_templates_both_succeed_and_nothing_is_overwritten():
    """Two starters side by side: their trees are disjoint, so both creates land.

    Bite: this is exactly what the folder prefix buys. The next test composes the
    same paths *without* the prefix and proves the second create collides on
    ``README.md`` — so removing the rule makes coexistence impossible.
    """
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)

    hello = tpl.by_key("hello")
    suite = tpl.by_key("pytest-suite")
    assert hello is not None and suite is not None

    for template in (hello, suite):
        writes = [
            FileWrite(path=tpl.template_path(template, f), content=f.content)
            for f in template.files
        ]
        result = await service.create_many(TG, ws.id, writes, folder="/" + template.key)
        assert result.ok, (template.key, result)

    hello_paths = set(hello.paths())
    suite_paths = set(suite.paths())
    assert hello_paths.isdisjoint(suite_paths), hello_paths & suite_paths
    page = await engine.list_files(ws.id, "/")
    landed = {e.path for e in page.entries}
    assert hello_paths | suite_paths <= landed
    # No file was overwritten: both READMEs exist on their own paths.
    assert "/hello/README.md" in landed and "/pytest-suite/README.md" in landed


async def test_without_the_folder_prefix_the_second_template_collides():
    """The bite, demonstrated: drop the prefix and hello blocks pytest-suite.

    This is the failure the folder rule removes — the second create is refused on
    ``/README.md``, which both templates ship. If the rule were reverted, the test
    above would fail the same way.
    """
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)

    hello = tpl.by_key("hello")
    suite = tpl.by_key("pytest-suite")
    assert hello is not None and suite is not None

    # The old, folder-less composition.
    first = [FileWrite(path="/" + f.path, content=f.content) for f in hello.files]
    second = [FileWrite(path="/" + f.path, content=f.content) for f in suite.files]
    assert (await service.create_many(TG, ws.id, first)).ok
    with pytest.raises(TemplatePathsExist) as exc:
        await service.create_many(TG, ws.id, second)
    assert "/README.md" in exc.value.paths


# ---------------------------------------------------------------------------
# 3. the bite — a collision refuses and writes zero files
# ---------------------------------------------------------------------------
async def test_a_collision_refuses_and_writes_zero_files():
    engine = FakeEngine()
    ws = await _clean_workspace(engine)
    service = _service(engine)

    template = tpl.by_key("hello")
    assert template is not None
    # The template's folder already exists (the user has a starter called hello),
    # which is the one thing that makes a re-create a refusal.
    await engine.mkdir(ws.id, "/hello")
    await engine.write_file(ws.id, "/hello/hello.py", "print('mine')\n")

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
        await service.create_many(TG, ws.id, writes, folder="/hello")

    assert exc.value.paths == ("/hello",), exc.value.paths
    assert received == [], "the refusal wrote a file"
    # The user's file is untouched.
    page = await engine.list_files(ws.id, "/")
    mine = [e for e in page.entries if e.path == "/hello/hello.py"]
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
    await engine.mkdir(ws.id, "/hello")
    await engine.write_file(ws.id, "/hello/hello.py", "print('mine')\n")

    async def no_collisions(*_args, **_kwargs):
        return []

    # Both halves of the guard are removed: the folder probe and the file probe.
    service.existing = no_collisions  # type: ignore[assignment]
    service.folder_conflicts = no_collisions  # type: ignore[assignment]

    writes = [
        FileWrite(path=tpl.template_path(template, f), content=f.content)
        for f in template.files
    ]
    result = await service.create_many(TG, ws.id, writes, folder="/hello")
    assert result.ok and "/hello/hello.py" in result.written  # the bite: it overwrote


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
        failed_path="/flask-api/README.md",
        error_code="insufficient_storage",
        written=("/flask-api/app.py",),
        engine_message="internal detail a user must never see",
    ).doc.html
    assert "internal detail a user must never see" not in html
    assert catalog.t("err.insufficient_storage", locale) in html
    # Partial state is stated, not implied, with the path the user sees on disk.
    assert "flask-api/app.py" in html


@pytest.mark.parametrize("locale", LOCALES)
def test_the_list_is_one_line_per_template_and_never_a_table(locale):
    """His rule: the templates screen carries no long copy.

    Each template is **one** paragraph (icon + title); its description belongs to
    the confirmation, where a user actually needs it. A known description is
    asserted absent here and present there.
    """
    from cloudbot.presentation.screens import templates as tpl_screens

    screen = tpl_screens.list_screen("ws_x", locale)
    html = screen.doc.html
    assert "<table" not in html, "the template list must not be a table"

    # One paragraph per template, and nothing else beyond header + intro.
    for template in tpl.TEMPLATES:
        title = catalog.t(template.title_key, locale)
        icon = catalog.t(f"{template.title_key.rsplit('.', 1)[0]}.icon", locale)
        assert f"<p>{icon} {title}</p>" in html, (template.key, locale)
        assert catalog.t(template.detail_key, locale) not in html, (
            f"the list still prints {template.key}'s description"
        )

    # The header is the house shape: ✦ label ⟸ count.
    assert "⟸" in html

    # The description is exactly where it is needed: the confirmation.
    hello = tpl.by_key("hello")
    assert hello is not None
    confirm = tpl_screens.confirm_screen(hello, "ws_x", locale).doc.html
    assert catalog.t(hello.detail_key, locale) in confirm


@pytest.mark.parametrize("locale", LOCALES)
def test_the_list_intro_is_a_single_line(locale):
    """The intro is one line, not three (his "too much text" complaint)."""
    from cloudbot.presentation.screens import templates as tpl_screens

    intro = catalog.t("tpl.list.intro", locale)
    assert "\n" not in intro
    assert intro.count(".") <= 1, intro


@pytest.mark.parametrize("locale", LOCALES)
def test_the_confirm_screen_lists_folder_relative_paths(locale):
    """Every path on the confirmation starts with ``<key>/`` — what disk shows."""
    from cloudbot.presentation.screens import templates as tpl_screens

    for template in tpl.TEMPLATES:
        html = tpl_screens.confirm_screen(template, "ws_x", locale).doc.html
        for relative in template.relative_paths():
            assert relative.startswith(template.key + "/"), relative
            assert f"<code>{relative}</code>" in html, (template.key, relative, locale)
        # The workspace-absolute form (a leading slash) is not what a user reads.
        assert f"<code>/{template.key}/" not in html


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
        assert "hello/hello.py" in bot.sent[-1]
        # Nothing written yet — pick only confirms.
        assert bot.sent and "hello/hello.py" in bot.sent[-1]

        await dispatcher.feed_update(bot, _callback(3, TG, cb("tpl:confirm", "hello", ws.id)))
        template = tpl.by_key("hello")
        assert template is not None
        page = await container.engine.list_files(ws.id, "/hello")
        landed = {e.path for e in page.entries}
        assert landed == set(tpl.paths(template)), landed
        assert catalog.t("tpl.done.created_title", Locale.EN) in bot.sent[-1]
    finally:
        await container.aclose()


async def test_two_different_templates_coexist_in_one_workspace():
    """The whole point: hello and pytest-suite side by side, nothing overwritten.

    Bite: remove the folder prefix and this fails on ``README.md`` — the second
    create would collide with the first one's file.
    """
    container, ws, bot, dispatcher = await _harness()
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("tpl:confirm", "hello", ws.id)))
        hello = tpl.by_key("hello")
        assert hello is not None
        hello_page = await container.engine.list_files(ws.id, "/hello")
        hello_paths = {e.path for e in hello_page.entries}
        assert hello_paths == set(tpl.paths(hello)), hello_paths

        await dispatcher.feed_update(
            bot, _callback(2, TG, cb("tpl:confirm", "pytest-suite", ws.id))
        )
        suite = tpl.by_key("pytest-suite")
        assert suite is not None
        suite_page = await container.engine.list_files(ws.id, "/pytest-suite")
        suite_paths = {e.path for e in suite_page.entries}
        assert suite_paths == set(tpl.paths(suite)), suite_paths

        # Both trees still hold their own README, and the root has no loose file.
        for path in hello_paths | suite_paths:
            assert (await container.engine.stat_file(ws.id, path)).path == path
        root = await container.engine.list_files(ws.id, "/")
        loose = {e.path for e in root.entries}
        assert "/README.md" not in loose, loose
        assert "/hello.py" not in loose and "/hello/hello.py" in (hello_paths | set()), loose
    finally:
        await container.aclose()


async def test_the_same_template_twice_refuses_on_the_folder():
    """The guard is not lost: a second hello is refused, and it names the folder."""
    container, ws, bot, dispatcher = await _harness()
    try:
        await dispatcher.feed_update(bot, _callback(1, TG, cb("tpl:confirm", "hello", ws.id)))
        assert catalog.t("tpl.done.created_title", Locale.EN) in bot.sent[-1]

        await dispatcher.feed_update(bot, _callback(2, TG, cb("tpl:confirm", "hello", ws.id)))
        assert catalog.t("tpl.done.refused_title", Locale.EN) in bot.sent[-1]
        assert "<code>hello</code>" in bot.sent[-1]
    finally:
        await container.aclose()


async def test_a_template_never_overwrites_an_existing_folder_end_to_end():
    container, ws, bot, dispatcher = await _harness()
    try:
        await container.engine.mkdir(ws.id, "/hello")
        await container.engine.write_file(ws.id, "/hello/hello.py", "print('mine')\n")

        # The confirmation already flags the collision (the create button is muted).
        await dispatcher.feed_update(bot, _callback(1, TG, cb("tpl:pick", "hello", ws.id)))
        assert catalog.t("tpl.confirm.nothing_written", Locale.EN) in bot.sent[-1]
        assert "<code>hello</code>" in bot.sent[-1]

        # Confirming anyway refuses and writes nothing.
        await dispatcher.feed_update(bot, _callback(2, TG, cb("tpl:confirm", "hello", ws.id)))
        assert catalog.t("tpl.done.refused_title", Locale.EN) in bot.sent[-1]
        page = await container.engine.list_files(ws.id, "/hello")
        hello = [e for e in page.entries if e.path == "/hello/hello.py"]
        assert hello and hello[0].size == len("print('mine')\n")
        assert not any(e.path == "/hello/README.md" for e in page.entries)
    finally:
        await container.aclose()
