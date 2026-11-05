"""The one codec for ``term:`` callback data — typed builders + a total parser.

The live ``IndexError`` in ``handlers/terminal.py`` (``on_list``) was the same
*family* as Bug A (TASK 07): a callback's arity did not match what the handler
indexed, and it was invisible until someone tapped the button.

Here the mechanism was subtler. Terminal callbacks carry a workspace id and a
session id, which together overflow Telegram's 64-byte ``callback_data`` cap, so
they are encoded through the generic content-addressed codec (CB-8): the compact
token is a hash, and the full key parts live in a **process-global, in-memory**
registry. The log shows the service restarted at 14:38:03 and the user tapped a
button drawn by the *previous* process at 14:44:47: the registry was empty,
``decode`` returned ``key=()``, and ``decode(cb.data).key[0]`` raised
``IndexError: tuple index out of range``. Nothing rendered.

To make that class impossible for the terminal this module is the **only** place
``term:`` callback data is built and parsed:

* every producer on a screen calls a typed builder here, never ``cb`` directly;
* every handler calls :func:`parse`, never ``decode(...).key[n]``.

:func:`parse` is **total and defensive**: an unknown/stale token, a malformed
callback or an unknown action returns an :class:`InvalidTermCallback`
(``valid is False``) instead of raising, so a stale button is answered with a
correctable screen — never a crash and never a silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from cloudbot.presentation.keyboards.callbacks import cb as _cb, decode

#: The callback-data namespace for the terminal.
TERM_PREFIX = "term"

# -- action keys (the segment after ``term:``) -------------------------------
LIST = "list"
OPEN = "open"
VIEW = "view"
REFRESH = "refresh"
CMD = "cmd"
EXIT = "exit"
SIGNAL = "signal"
CTRL_D = "ctrl_d"
RESIZE = "resize"
RSZ = "rsz"
RSZ_CUSTOM = "rsz_custom"
CLOSE = "close"
FORGET = "forget"

#: How many key parts each action must decode to. The parser validates the decoded
#: arity against this table, so a lost token (key ``()``) is caught, not indexed.
#: An unknown action is rejected outright.
_ARITY: dict[str, int] = {
    LIST: 1,
    OPEN: 1,
    VIEW: 2,
    REFRESH: 2,
    CMD: 2,
    EXIT: 2,
    SIGNAL: 3,
    CTRL_D: 2,
    RESIZE: 2,
    RSZ: 3,
    RSZ_CUSTOM: 2,
    CLOSE: 2,
    FORGET: 2,
}


@dataclass(frozen=True)
class TermCallback:
    """A parsed, valid ``term:`` callback."""

    action: str
    args: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return True

    @property
    def ws_id(self) -> str:
        return self.args[0] if self.args else ""

    @property
    def ses_id(self) -> str:
        return self.args[1] if len(self.args) > 1 else ""


@dataclass(frozen=True)
class InvalidTermCallback:
    """A callback that is not a well-formed ``term:`` action (stale or malformed)."""

    raw: str
    reason: str
    action: str = ""

    @property
    def valid(self) -> bool:
        return False


TermParseResult: TypeAlias = TermCallback | InvalidTermCallback


# -- builders ----------------------------------------------------------------
def list_(ws_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{LIST}", ws_id)


def open_terminal(ws_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{OPEN}", ws_id)


def view(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{VIEW}", ws_id, ses_id)


def refresh(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{REFRESH}", ws_id, ses_id)


def cmd(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{CMD}", ws_id, ses_id)


def exit_(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{EXIT}", ws_id, ses_id)


def signal(ws_id: object, ses_id: object, sig: str) -> str:
    return _cb(f"{TERM_PREFIX}:{SIGNAL}", ws_id, ses_id, sig)


def ctrl_d(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{CTRL_D}", ws_id, ses_id)


def resize(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{RESIZE}", ws_id, ses_id)


def rsz(ws_id: object, ses_id: object, spec: str) -> str:
    return _cb(f"{TERM_PREFIX}:{RSZ}", ws_id, ses_id, spec)


def rsz_custom(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{RSZ_CUSTOM}", ws_id, ses_id)


def close(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{CLOSE}", ws_id, ses_id)


def forget(ws_id: object, ses_id: object) -> str:
    return _cb(f"{TERM_PREFIX}:{FORGET}", ws_id, ses_id)


# -- parser ------------------------------------------------------------------
def _invalid(data: str, reason: str, action: str = "") -> InvalidTermCallback:
    return InvalidTermCallback(raw=data, reason=reason, action=action)


def parse(data: str) -> TermParseResult:
    """Parse any callback into a :class:`TermCallback` or :class:`InvalidTermCallback`.

    Never raises. The action segment is read from the data itself (so it is
    available even when the token is unknown), then the decoded arity is checked
    against :data:`_ARITY` — a stale token decodes to ``()`` and is rejected here
    rather than indexed by the handler.
    """
    if not isinstance(data, str) or not data.startswith(TERM_PREFIX + ":"):
        return _invalid(str(data), "not_term")
    parts = data.split(":", 2)
    if len(parts) < 2 or not parts[1]:
        return _invalid(data, "missing_action")
    action = parts[1]
    expected = _ARITY.get(action)
    if expected is None:
        return _invalid(data, "unknown_action", action)
    decoded = decode(data)
    key = decoded.key
    if len(key) != expected:
        # The token is unknown to this process (e.g. the button was drawn before a
        # restart): the full key parts are lost, so the button is out of date.
        return _invalid(data, "stale", action)
    return TermCallback(action, tuple(str(a) for a in key))
