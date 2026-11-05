"""Keyboards: intent→style is validated, disabled=True builds a DisabledButton."""

from __future__ import annotations

import pytest
from aiogram.types import DisabledButton, InlineKeyboardButton

from cloudbot.core.locale import Locale
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.keyboards.buttons import MAX_CALLBACK_BYTES, inline
from cloudbot.presentation.keyboards.callbacks import CallbackCodec
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
