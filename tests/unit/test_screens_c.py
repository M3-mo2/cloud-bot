"""TASK screens-c: every reachable error screen says what a user can DO.

Measured before this task: three of the four ordinary error screens named a
problem and stopped — ``err_not_found`` ("that item no longer exists"),
``err_unprocessable`` ("that request couldn't be accepted") and
``err_workspace_suspended`` ("this workspace is paused", with the recovery route
``ws:resume`` existing but never offered). Only ``err_unavailable`` already said
"please try again".

Each test here fails on that old copy:

* the per-screen tests require a concrete "what to do next" clause, not just a
  statement of the state;
* ``err_workspace_suspended`` must carry the real ``ws:resume`` button;
* the catalog-wide guard walks every ``err_`` screen in the product and requires
  an action phrase or a recovery button, which is what stops the next error
  screen being written without one.
"""

from __future__ import annotations

import pytest

from cloudbot.core.locale import Locale
from cloudbot.presentation import catalog as screen_catalog
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
from cloudbot.presentation.mapping import errors as error_mapping

LOCALES = (Locale.EN, Locale.AR)
SUSPENDED_WS = "ws_01M6HJASCWM9Z1PF755GT89NK5"

#: Substrings that make a sentence an **action** clause, per locale. They are
#: deliberately broad ("try", "go back", "pick", "resume", "tap", "adjust" …) so
#: a future screen has many valid ways to pass and only silence fails.
ACTION_PHRASES: dict[Locale, tuple[str, ...]] = {
    Locale.AR: (
        "المحاولة",
        "حاول",
        "أعد",
        "ارجع",
        "اختر",
        "استأنف",
        "استئناف",
        "اضغط",
        "عدّل",
        "افتح",
        "جدّد",
    ),
    Locale.EN: (
        "try again",
        "Try again",
        "Go back",
        "pick",
        "Pick",
        "Resume",
        "Tap",
        "Adjust",
        "Open",
        "Renew",
        "Please",
    ),
}

#: The only callback the shared error screen always has. Any other callback is a
#: **recovery route** offered to the user.
BASELINE_CALLBACKS = frozenset({"nav:menu", "nav:noop"})


def _buttons(screen) -> list:
    markup = screen.markup
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None) or []
    return [button for row in rows for button in row]


def _callbacks(screen) -> list[str]:
    return [
        button.callback_data
        for button in _buttons(screen)
        if getattr(button, "callback_data", None)
    ]


def _recovery_callbacks(screen) -> list[str]:
    return [cb for cb in _callbacks(screen) if cb not in BASELINE_CALLBACKS]


def _has_action(screen, locale: Locale) -> bool:
    html = screen.doc.html
    return any(phrase in html for phrase in ACTION_PHRASES[locale])


def _is_actionable(screen, locale: Locale) -> bool:
    """The catalog-wide rule: an action clause OR a recovery button."""
    return _has_action(screen, locale) or bool(_recovery_callbacks(screen))


def _build(name: str, locale: Locale):
    CallbackCodec()
    return screen_catalog.build(name, locale)


# -- 1. per screen: the new copy says what to do ----------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_not_found_says_where_to_go(locale):
    screen = _build("err_not_found", locale)
    assert _has_action(screen, locale)
    # And the useful destination is a button, not only prose.
    assert "ws:list" in _callbacks(screen)


@pytest.mark.parametrize("locale", LOCALES)
def test_unprocessable_says_what_would_make_it_acceptable(locale):
    screen = _build("err_unprocessable", locale)
    html = screen.doc.html
    if locale is Locale.AR:
        assert "عدّل الطلب" in html
    else:
        assert "Adjust the request" in html
    # Retry is the route that lets the user try the corrected request.
    assert "nav:refresh" in _callbacks(screen)


@pytest.mark.parametrize("locale", LOCALES)
def test_workspace_suspended_offers_the_real_resume_route(locale):
    screen = _build("err_workspace_suspended", locale)
    assert _has_action(screen, locale)
    resume = [cb for cb in _callbacks(screen) if cb.startswith("ws:resume:")]
    assert resume == [f"ws:resume:{SUSPENDED_WS}"]


def test_suspended_screen_without_an_id_does_not_draw_a_dead_resume():
    """A refusal with no workspace cannot resume anything; it points at the list."""
    screen = error_mapping.error_code_screen("workspace_suspended", Locale.EN)
    callbacks = _callbacks(screen)
    assert not any(cb.startswith("ws:resume") for cb in callbacks)
    assert "ws:list" in callbacks


@pytest.mark.parametrize("locale", LOCALES)
def test_unavailable_keeps_its_retry_shape_and_phrase(locale):
    screen = _build("err_unavailable", locale)
    assert _has_action(screen, locale)
    assert "nav:refresh" in _callbacks(screen)


# -- 2. the catalog-wide guard ---------------------------------------------
def test_every_error_screen_is_actionable():
    """Walk the product: every ``err_`` screen must say or offer a next step.

    This is the guard the task asks for. It fails on the pre-task copy: only
    ``err_unavailable`` carried a phrase; the other three were statements.
    """
    offenders = []
    for name in sorted(screen_catalog.SCREEN_BUILDERS):
        if not name.startswith("err_"):
            continue
        for locale in LOCALES:
            CallbackCodec()
            screen = screen_catalog.build(name, locale)
            if not _is_actionable(screen, locale):
                offenders.append((name, locale.value, screen.doc.html))
    assert not offenders, f"error screens with no action or recovery button: {offenders}"


def test_the_guard_rejects_the_old_copy():
    """Bite check: the pre-task sentences fail the same predicate, so the guard
    is not vacuous."""
    from cloudbot.presentation.rich.document import RichDoc

    old = {
        "not_found": ("هذا العنصر لم يعد موجودًا.", "That item no longer exists."),
        "unprocessable": ("تعذّر قبول هذا الطلب.", "That request couldn't be accepted."),
        "workspace_suspended": ("هذه المساحة متوقفة مؤقتًا.", "This workspace is paused."),
    }
    from cloudbot.presentation.screens.screen import Screen

    for code, (ar, en) in old.items():
        for locale, body in ((Locale.AR, ar), (Locale.EN, en)):
            # The old ``error_code_screen``: title + body + one Back row.
            doc = RichDoc(locale)
            doc.h(2, error_mapping.ERROR_KEYS[code][0])
            doc.p(body)
            from cloudbot.presentation.keyboards.buttons import inline
            from cloudbot.presentation.keyboards.markup import inline_markup
            from cloudbot.presentation.keyboards.styles import Intent

            screen = Screen(
                doc=doc,
                markup=inline_markup(
                    [[inline("Back", "nav:menu", intent=Intent.PRIMARY)]]
                ),
            )
            assert not _is_actionable(screen, locale), (code, locale.value)


# -- 3. the wording stays user-facing: no engine codes ----------------------
@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize(
    "name", ["err_not_found", "err_unprocessable", "err_workspace_suspended"]
)
def test_new_copy_names_no_engine_code(name, locale):
    CallbackCodec()
    html = screen_catalog.build(name, locale).doc.html
    for code in ("not_found", "unprocessable", "workspace_suspended", "ws_"):
        assert code not in html, (name, locale.value, code)
