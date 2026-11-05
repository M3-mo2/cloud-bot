"""Live check: the Bot API accepts the markup our screens build.

Opt in with ``CLOUDBOT_LIVE_TELEGRAM=1`` (needs ``CLOUDBOT_BOT_TOKEN`` in the env or
in ``.env``). Nothing is delivered: every request targets a bogus chat id, so the
answer is ``chat not found`` for an ACCEPTED markup and a parse error for a bad one —
the markup is parsed before the chat is looked up, which makes this safe and precise.

Why this exists: the bot shipped with ``style="link"`` on every URL button. Telegram
refuses the whole message for it, so a published tunnel was created and never shown,
while every local test (and the repo's own proof #7, which asserted
``style == "link"``) passed. No local rule can catch that — only the real API can.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
import pytest

LIVE = os.environ.get("CLOUDBOT_LIVE_TELEGRAM") == "1"

API_ROOT = "https://api.telegram.org"
#: A chat id that cannot exist, so nothing is ever delivered.
BOGUS_CHAT = 999999999999

pytestmark = pytest.mark.skipif(
    not LIVE, reason="set CLOUDBOT_LIVE_TELEGRAM=1 to run against the real Bot API"
)


def _token() -> str:
    token = os.environ.get("CLOUDBOT_BOT_TOKEN")
    if not token:
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            match = re.search(r"^CLOUDBOT_BOT_TOKEN=(.+)$", env.read_text(), re.M)
            token = match.group(1).strip() if match else None
    if not token:
        pytest.skip("no bot token available")
    return token


def _send(markup: dict) -> dict:
    """POST a message with ``markup`` and return the parsed answer."""
    url = f"{API_ROOT}/bot{_token()}/sendMessage"
    body = {"chat_id": BOGUS_CHAT, "text": "markup probe", "reply_markup": markup}
    response = httpx.post(url, json=body, timeout=20)
    return response.json()


def _assert_markup_accepted(what: str, markup: dict) -> None:
    """A good markup fails only because the chat does not exist."""
    answer = _send(markup)
    description = answer.get("description", "")
    assert "chat not found" in description, (
        f"{what}: Telegram refused the markup — {description!r}. "
        f"The whole message would fail, so the user would see nothing."
    )


def test_the_bots_real_markup_is_accepted_by_telegram() -> None:
    """Build the exact markup every screen produces and let Telegram judge it.

    ``inline`` is the single button factory, so exercising it over the real intent
    matrix is exercising every screen in the product.
    """
    from cloudbot.presentation.keyboards.buttons import inline
    from cloudbot.presentation.keyboards.styles import Intent

    for intent in Intent:
        if intent is Intent.MUTED:
            continue
        # (a) a callback button
        button = inline("action", "nav:noop", intent=intent)
        _assert_markup_accepted(
            f"callback/{intent.name}",
            {"inline_keyboard": [[button.model_dump(exclude_none=True)]]},
        )
        # (b) a URL button — the surface that shipped broken
        link = inline("Open 8080", url="https://example.trycloudflare.com", intent=intent)
        _assert_markup_accepted(
            f"url/{intent.name}",
            {"inline_keyboard": [[link.model_dump(exclude_none=True)]]},
        )


def test_style_link_is_still_rejected_by_telegram() -> None:
    """The premise of the fix, pinned. If Telegram ever accepts ``"link"`` this
    fails loudly, and we can revisit the decision instead of keeping a stale workaround.
    """
    answer = _send(
        {"inline_keyboard": [[{"text": "a", "url": "https://example.com", "style": "link"}]]}
    )
    assert "Invalid button style" in answer.get("description", ""), answer


def test_a_style_free_url_button_is_accepted() -> None:
    """The behaviour we now rely on: a URL button needs no ``style``."""
    _assert_markup_accepted(
        "url/no-style",
        {"inline_keyboard": [[{"text": "a", "url": "https://example.com"}]]},
    )
