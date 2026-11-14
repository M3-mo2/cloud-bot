"""The mandatory-subscription gate screen (user-facing).

One screen, built **entirely in blocks**: a rule, a heading, one line saying what
is required, one button per required chat (named by its title), and a final
«✅ تحقّقت» button that re-checks and admits on success.

Why blocks: Telegram refuses ``<tg-button callback_data=...>`` in the html form
(``BUTTON_URL_INVALID``) while the ``blocks`` form accepts ``callback_data``
(measured). A chat with a public username is a ``url`` button (the user can join
in one tap); one without is a ``callback_data`` button that explains the missing
link. The re-check button is always a callback, and is **disabled** for the press
window with the remaining seconds shown, so a user cannot hammer it into a 429.

An **unknown** chat is not a lie: the user is let through it, and the screen says
so plainly instead of claiming they have not joined.
"""

from __future__ import annotations

from cloudbot.core.locale import Locale
from cloudbot.domain.forcejoin import ForceJoinChat, GateStatus
from cloudbot.presentation.i18n.catalog import catalog
from cloudbot.presentation.rich.document import RichDoc
from cloudbot.presentation.screens.screen import Screen

#: The gate's own callback prefix. The gate middleware exempts it so the
#: «تحقّقت» button can always re-open the gate.
GATE_CALLBACK_PREFIX = "fj:"
GATE_CHECK = "fj:check"

def gate_chat_callback(chat_id: int) -> str:
    return f"fj:chat:{int(chat_id)}"

def gate_screen(
    status: GateStatus, locale: Locale, *, cooldown_s: float = 0.0
) -> Screen:
    """The one screen a gated user sees, in the product's visual language.

    ``cooldown_s`` is the seconds left in the «تحقّقت» press window; when positive
    the button is disabled and its label carries the remaining seconds.
    """
    doc = RichDoc(locale)
    rule = catalog.t("fj.rule", locale)
    doc.paragraph(rule)
    doc.heading(catalog.t("fj.title", locale))
    doc.paragraph(rule)
    doc.paragraph(
        catalog.t("fj.required", locale, count=len(status.missing) or len(status.required))
    )

    for chat in status.missing:
        doc.raw_buttons([_join_button(chat)])

    remaining = max(0, int(round(cooldown_s)))
    if remaining > 0:
        doc.raw_buttons(
            [
                {
                    "text": catalog.t("fj.check_wait", locale, seconds=str(remaining)),
                    "callback_data": GATE_CHECK,
                    "style": "success",
                    "disabled": True,
                }
            ]
        )
    else:
        doc.raw_buttons(
            [
                {
                    "text": catalog.t("fj.check", locale),
                    "callback_data": GATE_CHECK,
                    "style": "success",
                }
            ]
        )

    unknown = status.unknown_chats
    if unknown:
        # Plain, truthful: these chats could not be verified, so the user is let
        # through them — no claim about why (a rate limit is not the user's fault).
        doc.paragraph(catalog.t("fj.unknown_note", locale))

    doc.paragraph(catalog.t("fj.hint", locale))
    return Screen(doc=doc)

def _join_button(chat: ForceJoinChat) -> dict:
    label = chat.label
    link = chat.link()
    if link:
        return {"text": label, "url": link, "style": "primary"}
    return {
        "text": label,
        "callback_data": gate_chat_callback(chat.chat_id),
        "style": "primary",
    }
