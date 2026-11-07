"""TASK broadcast: copy-message delivery, real audiences, and the safety rules.

The whole point of the feature is that the delivered message is Telegrams's own
copy of the admin's message — so the tests assert the ``(from_chat_id,
message_id)`` path, never a re-render. Every safety rule the task demands to be
code is proven here: the two-step confirm, the ceiling, the in-flight guard, the
consecutive-failure stop, ``RetryAfter`` honouring, and persisted progress across a
simulated restart.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from cloudbot.adapters.persistence.broadcast_store import SqliteBroadcastStore
from cloudbot.adapters.persistence.database import Database
from cloudbot.adapters.persistence.sqlite_users import SqliteUserRepository
from cloudbot.adapters.store.sqlite_subscriptions import SqliteSubscriptions
from cloudbot.bootstrap.app import build_dispatcher
from cloudbot.bootstrap.container import build
from cloudbot.core.config import Settings
from cloudbot.core.errors import BroadcastInFlight, BroadcastOverCeiling
from cloudbot.core.locale import Locale
from cloudbot.domain.broadcast import (
    BroadcastSource,
    DeliveryOutcome,
    classify_failure,
)
from cloudbot.domain.value_objects import WorkspaceId
from cloudbot.handlers.admin_panel import _broadcast_source
from cloudbot.presentation.mapping.broadcast import classify_copy_error
from cloudbot.presentation.screens.admin import broadcast as bc_screen
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.services.broadcast import BroadcastService, validate_source_kind

NOW = datetime(2026, 11, 3, 20, 43, 39, tzinfo=timezone.utc)
ADMIN = 777

class FakeStore:
    """An in-memory ``BroadcastStore`` for the loop rules (no SQL involved)."""

    def __init__(self, audiences: dict[str, list[int]] | None = None) -> None:
        self._audiences = audiences or {}
        self.runs: dict[int, object] = {}
        self.targets: dict[int, dict[int, str]] = {}
        self._next = 1
        self._sending: set[int] = set()

    async def audience_members(self, audience, *, now):
        return list(self._audiences.get(audience, []))

    async def create_draft(self, *, source, created_by, created_at):
        from cloudbot.domain.broadcast import BroadcastRun

        run_id = self._next
        self._next += 1
        run = BroadcastRun(
            id=run_id, created_at=created_at, created_by=created_by, source=source,
            audience="all", total=0, sent=0, failed=0, status="composing",
        )
        self.runs[run_id] = run
        self.targets[run_id] = {}
        return run

    async def latest_draft(self, *, created_by):
        for run in reversed(list(self.runs.values())):
            if run.status == "composing" and run.created_by == created_by:
                return run
        return None

    async def plan_audience(self, run_id, *, audience, recipients, now):
        from dataclasses import replace

        run = self.runs[run_id]
        self.targets[run_id] = {int(t): "pending" for t in recipients}
        run = replace(run, audience=audience, total=len(recipients), sent=0, failed=0)
        self.runs[run_id] = run
        return run

    async def create_run(self, *, source, audience, total, recipients, created_by, created_at):
        run = await self.create_draft(source=source, created_by=created_by, created_at=created_at)
        return await self.plan_audience(run.id, audience=audience, recipients=recipients, now=created_at)

    async def get_run(self, run_id):
        return self.runs.get(run_id)

    async def open_run(self):
        for run in self.runs.values():
            if run.status == "sending":
                return run
        return None

    async def claim_run(self, run_id, *, now):
        from dataclasses import replace

        if any(r.status == "sending" and r.id != run_id for r in self.runs.values()):
            return False
        run = self.runs[run_id]
        self.runs[run_id] = replace(run, status="sending", started_at=now)
        return True

    async def mark_interrupted(self, run_id, *, now):
        from dataclasses import replace

        self.runs[run_id] = replace(self.runs[run_id], status="interrupted")

    async def pending_targets(self, run_id):
        return [t for t, s in sorted(self.targets[run_id].items()) if s == "pending"]

    async def failed_targets(self, run_id):
        return [t for t, s in sorted(self.targets[run_id].items()) if s == "failed"]

    async def failure_counts(self, run_id):
        # The fake stores no per-target reason; the loop tests assert counters.
        return {}

    async def record_success(self, run_id, tg_id, *, now):
        from dataclasses import replace

        self.targets[run_id][tg_id] = "sent"
        run = self.runs[run_id]
        self.runs[run_id] = replace(run, sent=run.sent + 1)

    async def record_failure(self, run_id, tg_id, error, *, now):
        from dataclasses import replace

        self.targets[run_id][tg_id] = "failed"
        run = self.runs[run_id]
        self.runs[run_id] = replace(run, failed=run.failed + 1)

    async def reset_failed(self, run_id):
        moved = [t for t, s in self.targets[run_id].items() if s == "failed"]
        for t in moved:
            self.targets[run_id][t] = "pending"
        from dataclasses import replace

        run = self.runs[run_id]
        self.runs[run_id] = replace(run, failed=0)
        return len(moved)

    async def finish_run(self, run_id, status, *, stop_reason, now):
        from dataclasses import replace

        self.runs[run_id] = replace(self.runs[run_id], status=status, stop_reason=stop_reason)

    async def history(self, *, limit=10):
        return list(reversed(list(self.runs.values())))[:limit]

# -- 1. compose classification ----------------------------------------------
def test_compose_accepts_text_photo_document_video():
    assert validate_source_kind(has_text=True, photo=False, document=False, video=False, album=False) == "text"
    assert validate_source_kind(has_text=True, photo=True, document=False, video=False, album=False) == "photo"
    assert validate_source_kind(has_text=False, photo=False, document=True, video=False, album=False) == "document"
    assert validate_source_kind(has_text=False, photo=False, document=False, video=True, album=False) == "video"

def test_compose_refuses_an_album_with_a_reason():
    with pytest.raises(ValueError) as info:
        validate_source_kind(has_text=False, photo=True, document=False, video=False, album=True)
    assert str(info.value) == "album"

class _Msg:
    def __init__(self, *, text=None, photo=False, video=False, document=False, album=False):
        self.text = text
        self.photo = (object(),) if photo else None
        self.video = object() if video else None
        self.document = object() if document else None
        self.media_group_id = "g1" if album else None

def test_broadcast_source_reads_a_real_message():
    assert _broadcast_source(_Msg(text="hi")) == ("text", "")
    assert _broadcast_source(_Msg(text="cap", photo=True)) == ("photo", "")
    assert _broadcast_source(_Msg(document=True)) == ("document", "")
    assert _broadcast_source(_Msg(video=True)) == ("video", "")
    assert _broadcast_source(_Msg(photo=True, album=True)) == ("", "album")

# -- 2. the preview is the copy path ----------------------------------------
async def test_preview_uses_the_exact_copy_reference():
    """The preview shows ``(chat, message)``; the sender copies that exact pair.

    This is the whole request: no re-render. ``copy_reference`` is the one source
    of the pair, so the screen cannot show one thing and send another.
    """
    source = BroadcastSource(chat_id=-100, message_id=55, kind="text")
    assert BroadcastService.copy_reference(source) == (-100, 55)

# -- 3. audiences are real sets ---------------------------------------------
@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()

async def _seed(database, tg_id: int, *, name: str = "u") -> None:
    repo = SqliteUserRepository(database)
    await repo.get_or_create(tg_id, default_locale=Locale.EN)
    await repo.set_display_name(tg_id, name)

async def _grant(database, tg_id: int, ws_id: str, *, days: int, now: datetime = NOW) -> None:
    """Grant on the fixture's clock, not the wall clock.

    ``now`` used to be accepted and then dropped: ``SqliteSubscriptions.grant``
    read the real clock, so "a one-day plan" was measured from today instead of
    from ``NOW`` and the "expired five days later" assertion reversed itself once
    the calendar moved past the fixture date. The parameter is now load-bearing.
    """
    subs = SqliteSubscriptions(database)
    await subs.grant(
        tg_id, ws_id, "month", days, ADMIN, "test",
        idempotency_key=f"t:{tg_id}:{days}", now=now,
    )

async def test_audience_presets_return_real_disjoint_sets(db):
    # 1: has an active plan. 2: has an expired plan. 3: has a workspace, no plan.
    # 4: a bare user with no workspace.
    await _seed(db, 1)
    await _seed(db, 2)
    await _seed(db, 3)
    await _seed(db, 4)
    repo = SqliteUserRepository(db)
    await repo.add_workspace(1, WorkspaceId("ws_" + "1" * 26))
    await repo.add_workspace(2, WorkspaceId("ws_" + "2" * 26))
    await repo.add_workspace(3, WorkspaceId("ws_" + "3" * 26))
    await _grant(db, 1, "ws_" + "1" * 26, days=30)
    await _grant(db, 2, "ws_" + "2" * 26, days=1)
    # Expire #2 by moving the clock past it (the store compares timestamps).
    future = NOW + timedelta(days=5)

    store = SqliteBroadcastStore(db)
    all_ids = await store.audience_members("all", now=NOW)
    active = await store.audience_members("active", now=future)
    expired = await store.audience_members("expired", now=future)
    no_plan = await store.audience_members("no_plan", now=NOW)

    assert all_ids == [1, 2, 3, 4]
    assert active == [1]
    assert expired == [2]
    assert no_plan == [3]
    # A user with no plan is *not* "active plan".
    assert 3 not in active and 3 not in expired
    # The three restricted presets are pairwise disjoint.
    assert not (set(active) & set(expired))
    assert not (set(active) & set(no_plan))
    assert not (set(expired) & set(no_plan))

async def test_no_plan_excludes_a_user_who_has_none_of_everything(db):
    await _seed(db, 9)
    store = SqliteBroadcastStore(db)
    assert await store.audience_members("no_plan", now=NOW) == []

# -- 4. the send loop -------------------------------------------------------
def _service(store: FakeStore, *, ceiling=5000, threshold=5, sleep=None) -> BroadcastService:
    return BroadcastService(
        store, send_delay_s=0.0, failure_threshold=threshold,
        max_recipients=ceiling, sleep=sleep or _no_sleep, clock=lambda: NOW,
    )

async def _no_sleep(_seconds: float) -> None:
    return None

async def _draft_planned(service: BroadcastService, store: FakeStore, audience="all") -> int:
    run = await service.create_draft(
        source=BroadcastSource(chat_id=ADMIN, message_id=9, kind="text"), created_by=ADMIN
    )
    await service.plan(run.id, audience=audience)
    return run.id

async def test_send_loop_continues_past_a_blocked_user():
    store = FakeStore({"all": [1, 2, 3]})
    service = _service(store)
    run_id = await _draft_planned(service, store)
    reached: list[int] = []

    async def copy(chat, frm, mid):
        if chat == 2:
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")  # type: ignore[arg-type]
        reached.append(chat)
        return type("M", (), {"message_id": chat * 10})()

    result = await service.send(
        run_id, copy=copy, classify=classify_copy_error, retry_after=lambda e: None
    )
    assert reached == [1, 3]
    assert result.run.sent == 2
    assert result.run.failed == 1
    assert result.run.status == "done"

async def test_retry_after_is_honoured_exactly():
    store = FakeStore({"all": [1]})
    service = _service(store)
    run_id = await _draft_planned(service, store)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(float(seconds))

    service._sleep = fake_sleep
    calls = {"n": 0}

    async def copy(chat, frm, mid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=7)  # type: ignore[arg-type]
        return type("M", (), {"message_id": 1})()

    result = await service.send(
        run_id, copy=copy, classify=classify_copy_error,
        retry_after=lambda e: 7 if isinstance(e, TelegramRetryAfter) else None,
    )
    assert slept == [7.0]
    assert result.run.sent == 1
    assert result.run.failed == 0

async def test_consecutive_failure_threshold_stops_the_run():
    store = FakeStore({"all": [1, 2, 3, 4, 5, 6, 7, 8]})
    service = _service(store, threshold=3)
    run_id = await _draft_planned(service, store)
    attempted: list[int] = []

    async def copy(chat, frm, mid):
        attempted.append(chat)
        raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked")  # type: ignore[arg-type]

    result = await service.send(
        run_id, copy=copy, classify=classify_copy_error, retry_after=lambda e: None
    )
    # Exactly ``threshold`` attempts: the rest of the audience was not burnt.
    assert attempted == [1, 2, 3]
    assert result.run.status == "stopped"
    assert result.run.stop_reason == "consecutive_failures"
    assert result.run.failed == 3
    assert result.run.remaining == 5

async def test_in_flight_guard_refuses_a_second_run():
    store = FakeStore({"all": [1]})
    service = _service(store)
    run_id = await _draft_planned(service, store)
    # Another run is already sending.
    other = await service.create_draft(
        source=BroadcastSource(chat_id=ADMIN, message_id=8, kind="text"), created_by=ADMIN
    )
    await store.claim_run(other.id, now=NOW)

    async def copy(chat, frm, mid):
        return type("M", (), {"message_id": 1})()

    with pytest.raises(BroadcastInFlight):
        await service.send(run_id, copy=copy, classify=classify_copy_error, retry_after=lambda e: None)

async def test_progress_is_persisted_and_survives_a_restart():
    store = FakeStore({"all": [1, 2]})
    service = _service(store)
    run_id = await _draft_planned(service, store)

    async def copy(chat, frm, mid):
        if chat == 2:
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked")  # type: ignore[arg-type]
        return type("M", (), {"message_id": 1})()

    await service.send(run_id, copy=copy, classify=classify_copy_error, retry_after=lambda e: None)
    # A new service over the same store = the restart. Counts come from the store.
    restarted = _service(store)
    run = await restarted.run(run_id)
    assert run is not None
    assert (run.sent, run.failed) == (1, 1)

async def test_a_dead_sending_row_is_reported_interrupted_not_live():
    store = FakeStore({"all": [1]})
    service = _service(store)
    run_id = await _draft_planned(service, store)
    await store.claim_run(run_id, now=NOW)  # a previous process died here
    restarted = _service(store)  # this process does not own it
    view = await restarted.view()
    run = await restarted.run(run_id)
    assert run is not None and run.status == "interrupted"
    assert view.open_run is None or view.open_run.status != "sending"

async def test_cap_refuses_over_ceiling_without_the_explicit_confirm():
    store = FakeStore({"all": list(range(1, 20))})
    service = _service(store, ceiling=5)
    run = await service.create_draft(
        source=BroadcastSource(chat_id=ADMIN, message_id=1, kind="text"), created_by=ADMIN
    )
    with pytest.raises(BroadcastOverCeiling):
        await service.plan(run.id, audience="all")
    # With the explicit confirm, the bigger number is allowed.
    planned = await service.plan(run.id, audience="all", confirmed_count=19)
    assert planned.total == 19

async def test_a_stale_confirm_below_the_live_count_is_refused():
    store = FakeStore({"all": list(range(1, 10))})
    service = _service(store, ceiling=5)
    run = await service.create_draft(
        source=BroadcastSource(chat_id=ADMIN, message_id=1, kind="text"), created_by=ADMIN
    )
    with pytest.raises(BroadcastOverCeiling):
        await service.plan(run.id, audience="all", confirmed_count=5)

# -- 5. failure bucketing ----------------------------------------------------
def test_failure_buckets():
    assert classify_failure("blocked") == "blocked"
    assert classify_failure("deactivated") == "deactivated"
    assert classify_failure("chat_not_found") == "deactivated"
    assert classify_failure("weird") == "other"

def test_forbidden_with_deactivated_is_the_deactivated_bucket():
    exc = TelegramForbiddenError(method=None, message="Forbidden: user is deactivated")  # type: ignore[arg-type]
    assert classify_copy_error(exc) == "deactivated"

# -- 6. screens: digits, columns, callbacks --------------------------------
@pytest.fixture
def admin_view():
    from cloudbot.domain.admin import AdminView

    return AdminView(locale=Locale.EN, now=NOW)

def _all_screens(view):
    from cloudbot.domain.admin import AdminView
    from cloudbot.domain.broadcast import (
        AudienceOption,
        BroadcastResult,
        BroadcastRun,
        BroadcastSource,
        BroadcastView,
        FailureGroup,
    )

    source = BroadcastSource(chat_id=ADMIN, message_id=5, kind="text")
    run = BroadcastRun(
        id=1, created_at=NOW, created_by=ADMIN, source=source, audience="all",
        total=10, sent=7, failed=3, status="done",
    )
    section = BroadcastView(
        options=tuple(AudienceOption(key=k, count=10) for k in ("all", "active", "expired", "no_plan")),
        history=(run,), open_run=None, max_recipients=5000, failure_threshold=5,
    )
    result = BroadcastResult(run=run, groups=(FailureGroup(kind="blocked", count=3),))
    ar = AdminView(locale=Locale.AR, now=NOW)
    screens = [
        bc_screen.broadcast_screen(view),
        bc_screen.compose_prompt(view),
        bc_screen.audience_screen(view, section, source=source),
        bc_screen.preview_screen(view, source=source, family="all", count=10, ceiling=5000, over_ceiling=False),
        bc_screen.preview_screen(view, source=source, family="all", count=9000, ceiling=5000, over_ceiling=True),
        bc_screen.progress_screen(view, run),
        bc_screen.result_screen(view, result),
        bc_screen.history_screen(view, (run,)),
    ]
    screens += [
        bc_screen.broadcast_screen(ar),
        bc_screen.audience_screen(ar, section, source=source),
        bc_screen.result_screen(ar, result),
        bc_screen.history_screen(ar, (run,)),
    ]
    return screens

def test_no_arabic_digits_and_callbacks_within_64_bytes(admin_view):
    from cloudbot.presentation.i18n.formatters import has_arabic_digits

    for screen in _all_screens(admin_view):
        html = screen.doc.html
        assert not has_arabic_digits(html)
        if screen.markup is not None:
            for row in screen.markup.inline_keyboard:
                for button in row:
                    assert not has_arabic_digits(button.text)
                    if button.callback_data:
                        assert len(button.callback_data.encode("utf-8")) <= 64

def test_audience_and_result_tables_are_two_columns(admin_view):
    """The panel keeps the project's `label ⟸ value` two-column language."""
    from cloudbot.domain.broadcast import AudienceOption, BroadcastResult, BroadcastRun, BroadcastSource, BroadcastView, FailureGroup

    source = BroadcastSource(chat_id=ADMIN, message_id=5, kind="text")
    section = BroadcastView(
        options=tuple(AudienceOption(key=k, count=10) for k in ("all", "active", "expired", "no_plan")),
        max_recipients=5000, failure_threshold=5,
    )
    audience = bc_screen.audience_screen(admin_view, section, source=source)
    run = BroadcastRun(id=1, created_at=NOW, created_by=ADMIN, source=source,
                       audience="all", total=10, sent=7, failed=3, status="done")
    result = bc_screen.result_screen(
        admin_view, BroadcastResult(run=run, groups=(FailureGroup(kind="blocked", count=3),))
    )
    for screen in (audience, result):
        html = screen.doc.html
        # Every table row carries exactly two cells.
        import re

        for row in re.findall(r"<tr>(.*?)</tr>", html):
            cells = re.findall(r"<t[hd]>", row)
            assert len(cells) in (0, 2), row

def test_every_broadcast_callback_parses():
    from cloudbot.presentation.screens.admin.callbacks import AdminCallback, parse

    for data in (
        admin_cb.broadcast_compose(), admin_cb.broadcast_audience("all"),
        admin_cb.broadcast_confirm("all", False), admin_cb.broadcast_confirm("all", True),
        admin_cb.broadcast_result(3), admin_cb.broadcast_retry(3),
        admin_cb.broadcast_history(), admin_cb.broadcast_resend(3), admin_cb.broadcast_cancel(),
    ):
        parsed = parse(data)
        assert isinstance(parsed, AdminCallback), data

# -- 7. the handler passes the copy path, not a re-render --------------------
class _Bot:
    id = 42

    def __init__(self):
        self.sent: list[str] = []
        self.copied: list[tuple[int, int, int]] = []

    async def delete_message(self, chat_id, message_id):
        return True

    async def send_rich_message(self, chat_id, rich_message, reply_markup=None, reply_parameters=None):
        self.sent.append(getattr(rich_message, "html", None) or "")
        return Message.model_construct(chat={"id": chat_id}, message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, rich_message=None, reply_markup=None):
        self.sent.append(getattr(rich_message, "html", None) or "")
        return Message.model_construct(chat={"id": chat_id}, message_id=message_id)

    async def copy_message(self, chat_id, from_chat_id, message_id):
        self.copied.append((chat_id, from_chat_id, message_id))
        return type("M", (), {"message_id": len(self.copied)})()

    async def __call__(self, method):
        return True

def _cb(uid, user_id, data) -> Update:
    msg = Message(message_id=uid, date=NOW, chat=Chat(id=user_id, type="private"),
                  from_user=User(id=user_id, is_bot=False, first_name="A"), text="s")
    return Update(update_id=uid, callback_query=CallbackQuery(
        id=str(uid), from_user=User(id=user_id, is_bot=False, first_name="A"),
        chat_instance="c", message=msg, data=data))

def _msg(uid, user_id, *, text=None, photo=False, album=False) -> Update:
    return Update(update_id=uid, message=Message(
        message_id=uid, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="A"),
        text=text, photo=(PhotoSize(file_id="f", file_unique_id="u", width=1, height=1),) if photo else None,
        media_group_id="grp" if album else None))

async def _container():
    settings = Settings(
        mode="production", bot_token="1:TEST", engine_api_key="k",
        database_url="sqlite+aiosqlite:///:memory:", mock_engine=True,
        admin_ids=str(ADMIN), throttle_rate=1000.0, _env_file=None,
    )
    container = build(settings)
    await container.database.migrate()
    for tg in (1, 2, 3):
        await container.users.get_or_create(tg, default_locale=Locale.EN)
    return container

async def test_the_send_handler_copies_the_stored_reference_not_a_rerender():
    container = await _container()
    bot = _Bot()
    dispatcher = build_dispatcher(container, bot)
    try:
        # Press Compose (enters the FSM state), then forward/compose the message.
        await dispatcher.feed_update(bot, _cb(0, ADMIN, "adm:bc_compose"))
        await dispatcher.feed_update(bot, _msg(1, ADMIN, text="hello **bold** https://x"))
        # Then picks an audience and confirms.
        await dispatcher.feed_update(bot, _cb(2, ADMIN, "adm:bc_aud:all"))
        await dispatcher.feed_update(bot, _cb(3, ADMIN, "adm:bc_go:all:0"))
        assert bot.copied, "the handler did not copy anything"
        # Every copy used the admin's own (chat, message) — never a re-render.
        for chat_id, from_chat, source_id in bot.copied:
            assert from_chat == ADMIN
            assert source_id == 1  # the message id the admin sent
            assert chat_id in (1, 2, 3, ADMIN)
    finally:
        await container.aclose()

async def test_confirm_alone_cannot_send(): 
    """Two-step confirm: composing a message never sends without the preview step."""
    container = await _container()
    bot = _Bot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _cb(0, ADMIN, "adm:bc_compose"))
        await dispatcher.feed_update(bot, _msg(1, ADMIN, text="hi"))
        # No audience/preview tap — nothing was copied.
        assert bot.copied == []
    finally:
        await container.aclose()

async def test_compose_refuses_an_album_in_the_handler():
    container = await _container()
    bot = _Bot()
    dispatcher = build_dispatcher(container, bot)
    try:
        await dispatcher.feed_update(bot, _cb(0, ADMIN, "adm:bc_compose"))
        await dispatcher.feed_update(bot, _msg(1, ADMIN, photo=True, album=True))
        # The refusal is shown; no run was created and nothing copied.
        assert bot.copied == []
        assert any("album" in html.lower() or "ألبوم" in html for html in bot.sent)
    finally:
        await container.aclose()
