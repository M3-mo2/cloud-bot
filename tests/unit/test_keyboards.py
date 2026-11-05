"""Keyboards: intent→style is validated, disabled=True builds a DisabledButton."""

from __future__ import annotations

import pytest
from aiogram.types import DisabledButton, InlineKeyboardButton

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import MAX_CALLBACK_BYTES, inline
from cloudbot.presentation.keyboards.callbacks import (
    CallbackCodec,
    StaleCallback,
    decode_required,
)
from cloudbot.presentation.keyboards.styles import (
    ALLOWED_STYLES,
    Intent,
    InvalidIntent,
    style_for,
)


def test_only_three_styles_exist():
    # "link" is NOT a style Telegram accepts: sending it on any inline button fails
    # the whole message with "can't parse InlineKeyboardButton: Invalid button style
    # specified" (measured against the live Bot API). A URL button carries no style.
    assert ALLOWED_STYLES == {"primary", "success", "danger"}


def test_intent_to_style():
    assert style_for(Intent.PRIMARY) == "primary"
    assert style_for(Intent.SUCCESS) == "success"
    assert style_for(Intent.DANGER) == "danger"
    # A LINK intent keeps its meaning but maps to NO style — see above.
    assert style_for(Intent.LINK) is None
    assert style_for(Intent.MUTED) is None


def test_bad_style_is_rejected_locally():
    # A typo could not be caught by the type system; our helper catches it.
    with pytest.raises(InvalidIntent):
        style_for("red")  # type: ignore[arg-type]


def test_url_button_carries_no_style():
    """The regression guard for the live defect.

    Every tunnel LINK button went out as ``style="link"``, which Telegram refuses for
    the whole message — so a published tunnel was created and never shown. A URL
    button must carry no ``style`` at all.
    """
    button = inline("Open 8080", url="https://x.trycloudflare.com", intent=Intent.LINK)
    assert button.url == "https://x.trycloudflare.com"
    assert button.style is None, f"a URL button must carry no style, got {button.style!r}"
    assert button.callback_data is None


def test_inline_button_styles():
    button = inline("Go", "ws:list:0", intent=Intent.SUCCESS)
    assert button.style == "success"
    assert button.callback_data == "ws:list:0"


def test_disabled_uses_instance_not_bool():
    button = inline("Run", "run:choose:x", intent=Intent.MUTED)
    assert isinstance(button.disabled, DisabledButton)


def test_disabled_boolean_builds_object():
    button = inline("Run", "x", disabled=True)
    assert isinstance(button.disabled, DisabledButton)
    # The raw bool would have raised; assert the serialized marker is present.
    assert "disabled" in button.model_dump()
    assert button.model_dump()["disabled"] == {}


@pytest.mark.parametrize("locale", [Locale.EN, Locale.AR])
def test_disabled_marker_present_each_locale(locale):
    text = catalog.t("ws.run", locale)
    button = inline(text, "run:choose:x", intent=Intent.MUTED, disabled=True)
    dumped = button.model_dump()
    assert dumped["disabled"] == {}
    assert dumped["text"] == text


def test_raw_bool_disabled_raises_the_measured_way():
    """Regression guard: passing disabled=True raises, so the helper must build
    a DisabledButton itself (this is the measured aiogram 3.31.0 fact)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InlineKeyboardButton(text="x", callback_data="y", disabled=True)


def test_callback_codec_under_64_bytes():
    codec = CallbackCodec()
    data = codec.encode("run:logs", "ws_01M6HJASCWM9Z1PF755GT89NK4", "run_01M6HJASCWM9Z1PF755GT89NK4")
    assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES
    decoded = codec.decode(data)
    assert decoded.action == "run:logs"
    assert decoded.key == ("ws_01M6HJASCWM9Z1PF755GT89NK4", "run_01M6HJASCWM9Z1PF755GT89NK4")


def test_callback_codec_is_deterministic():
    a = CallbackCodec().encode("ws:view", "ws_abc")
    b = CallbackCodec().encode("ws:view", "ws_abc")
    assert a == b


def test_callback_codec_raises_in_dev():
    from cloudbot.core.modes import RunMode

    codec = CallbackCodec(mode=RunMode.DEV, max_bytes=4)
    with pytest.raises(Exception):
        codec.encode("toolongaction")


def test_callback_codec_inlines_short_keys_and_survives_a_restart():
    """A callback that fits the cap carries its key parts inline (CB-8, TASK 23).

    Inline means the key parts travel in the callback itself, so a restart — which
    empties the token registry — cannot affect them.
    """
    codec = CallbackCodec()
    data = codec.encode("ws:view", "ws_01M6HJASCWM9Z1PF755GT89NK4")
    assert data == "ws:view:ws_01M6HJASCWM9Z1PF755GT89NK4", data
    assert "~" not in data, "a short callback must not use the token registry"
    codec.reset()  # the restart
    decoded = codec.decode(data)
    assert decoded.action == "ws:view"
    assert decoded.key == ("ws_01M6HJASCWM9Z1PF755GT89NK4",)


def test_callback_codec_tokens_only_what_cannot_fit():
    """A callback too long to inline falls back to a token; after a reset the key
    is gone (and ``decode`` is total: it returns ``()``, it does not raise)."""
    from cloudbot.presentation.keyboards.callbacks import codec as global_codec

    global_codec.reset()
    ws = "ws_01M6HJASCWM9Z1PF755GT89NK4"
    ses = "ses_01M6HJATZTFXTWXWBG8A5KWYS8"
    data = global_codec.encode("term:view", ws, ses)
    assert data.startswith("term:view:~"), data
    assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES
    assert global_codec.decode(data).key == (ws, ses)
    global_codec.reset()
    assert global_codec.decode(data).key == ()
    # ``decode`` stayed total; the guarded read raises instead of indexing.
    with pytest.raises(StaleCallback):
        decode_required(data, 2)


def test_decode_required_raises_stale_for_a_short_or_unknown_callback():
    with pytest.raises(StaleCallback):
        decode_required("ws:view", 1)  # no key part at all
    with pytest.raises(StaleCallback):
        decode_required("term:view:~deadbeef", 2)  # unknown token (a restart)


def test_stale_callback_maps_to_a_correctable_screen_both_locales():
    """The guard renders the stale screen — never an ``IndexError`` and never
    silence, in either locale."""
    from cloudbot.presentation.mapping.errors import error_screen

    for locale, marker in ((Locale.EN, "out of date"), (Locale.AR, "قديم")):
        screen = error_screen(StaleCallback("term:view:~deadbeef"), locale)
        assert marker in screen.doc.html, (locale, screen.doc.html[:120])
