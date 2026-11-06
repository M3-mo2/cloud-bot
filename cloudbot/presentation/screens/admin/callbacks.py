"""The one codec for ``adm:`` callback data — typed builders + a total parser.

Bug A (TASK 07) was a dead button: the per-user audit button was assembled with a
bare f-string that omitted the page, while the handler read ``parts[3]`` and raised
``IndexError``. The button never rendered anything and the user saw nothing. The
class of bug is what "adding a button" does when the callback shape is a bare
f-string on the producer side and ``split(":")[n]`` on the consumer side.

To make that class impossible this module is the **only** place ``adm:`` callback
data is built and parsed:

* every producer on a screen calls a typed builder here (e.g.
  ``audit_user(tg_id, page)``), never an f-string;
* every handler calls :func:`parse`, never ``cb.data.split``.

:func:`parse` is **total and defensive**: a malformed, short or unknown callback
returns an :class:`InvalidAdminCallback` (``valid is False``) instead of raising, so
a stale button is answered with a correctable screen — never a crash and never a
silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

#: The callback-data namespace for the panel. The admin gate keys off ``adm:``.
ADMIN_PREFIX = "adm"

# -- action keys (the segment after ``adm:``) --------------------------------
MENU = "menu"
SECTION = "sec"
USERS_PAGE = "users_page"
USERS_SEARCH = "users_search"
USER = "user"
ASK = "ask"
CANCEL = "cancel"
LEAVE = "leave"
NOTIFY_TOGGLE = "notify_toggle"
NOTIFY_ALL = "notify_all"
NOTIFY_PAUSE = "notify_pause"
NOTIFY_SCOPE = "notify_scope"
AUDIT_CAT = "audit_cat"
AUDIT_ALL = "audit_all"
AUDIT = "audit"
AUDIT_USER = "audit_user"
# -- wallet + subscription controls -----------------------------------------
WALLET = "wallet"
WALLET_OP = "wop"
WALLET_PLAN = "wplan"

#: The admin workspace actions declared in ``actions.py``. Each takes one subject.
ACTION_KEYS: frozenset[str] = frozenset({"enter", "suspend", "resume", "delete"})


@dataclass(frozen=True)
class AdminCallback:
    """A parsed, valid ``adm:`` callback."""

    action: str
    args: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return True

    def arg_int(self, index: int = 0) -> int:
        return int(self.args[index])

    @property
    def subject(self) -> str:
        return self.args[0] if self.args else ""


@dataclass(frozen=True)
class InvalidAdminCallback:
    """A callback that is not a well-formed ``adm:`` action (or is out of date)."""

    raw: str
    reason: str
    action: str = ""
    args: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return False


AdminParseResult: TypeAlias = AdminCallback | InvalidAdminCallback


# -- builders ----------------------------------------------------------------
def _action(name: str, *args: object) -> str:
    segments = [ADMIN_PREFIX, name, *(str(a) for a in args)]
    return ":".join(segments)


def menu() -> str:
    return _action(MENU)


def section(key: str) -> str:
    return _action(SECTION, key)


def users_page(page: int) -> str:
    return _action(USERS_PAGE, int(page))


def users_search() -> str:
    return _action(USERS_SEARCH)


def user(tg_id: int) -> str:
    return _action(USER, int(tg_id))


def ask(key: str, subject: str) -> str:
    return _action(ASK, key, subject)


def cancel(subject: str) -> str:
    return _action(CANCEL, subject)


def action(key: str, subject: str) -> str:
    """A declared admin action's *performing* callback (``adm:enter:<subject>``)."""
    return _action(key, subject)


def leave() -> str:
    return _action(LEAVE)


def notify_toggle(category: str) -> str:
    return _action(NOTIFY_TOGGLE, category)


def notify_all(enabled: bool) -> str:
    return _action(NOTIFY_ALL, "on" if enabled else "off")


def notify_pause(paused: bool) -> str:
    return _action(NOTIFY_PAUSE, "on" if paused else "off")


def notify_scope(tg_id: int | None) -> str:
    return _action(NOTIFY_SCOPE, "none" if tg_id is None else int(tg_id))


def audit_cat(category: str, page: int = 0) -> str:
    return _action(AUDIT_CAT, category, int(page))


def audit_all(tg_id: int | None, page: int = 0) -> str:
    if tg_id is None:
        return _action(AUDIT_ALL, "global", int(page))
    return _action(AUDIT_ALL, "user", int(tg_id), int(page))


def audit(page: int = 0) -> str:
    return _action(AUDIT, int(page))


def audit_user(tg_id: int, page: int = 0) -> str:
    return _action(AUDIT_USER, int(tg_id), int(page))

def wallet(tg_id: int) -> str:
    """Open a user's wallet + subscription controls."""
    return _action(WALLET, int(tg_id))

def wallet_op(op: str, tg_id: int, step: str = "ask", arg: str = "") -> str:
    """A wallet mutation. ``step`` is ``ask`` (prompt) or ``go`` (perform with ``arg``)."""
    return _action(WALLET_OP, op, int(tg_id), step, arg)

def wallet_plan(op: str, tg_id: int, plan_code: str, step: str = "ask", arg: str = "") -> str:
    return _action(WALLET_PLAN, op, int(tg_id), plan_code, step, arg)


# -- parser ------------------------------------------------------------------
def _invalid(data: str, reason: str, action: str = "") -> InvalidAdminCallback:
    return InvalidAdminCallback(raw=data, reason=reason, action=action)


def _require_int(data: str, value: str, action: str) -> int | InvalidAdminCallback:
    try:
        return int(value)
    except (TypeError, ValueError):
        return _invalid(data, "not_an_int", action)


def parse(data: str) -> AdminParseResult:
    """Parse any callback into an :class:`AdminCallback` or :class:`InvalidAdminCallback`.

    Never raises.
    """
    if not isinstance(data, str) or not data.startswith(ADMIN_PREFIX + ":"):
        return _invalid(str(data), "not_admin")
    parts = data.split(":")
    if len(parts) < 2 or not parts[1]:
        return _invalid(data, "missing_action")
    action = parts[1]
    rest = parts[2:]

    if action in (MENU, USERS_SEARCH, LEAVE):
        if rest:
            return _invalid(data, "too_many_args", action)
        return AdminCallback(action)

    if action == SECTION:
        if len(rest) != 1 or not rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action in (USERS_PAGE, AUDIT, USER):
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        number = _require_int(data, rest[0], action)
        if isinstance(number, InvalidAdminCallback):
            return number
        return AdminCallback(action, (str(number),))

    if action == NOTIFY_SCOPE:
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        raw = rest[0]
        if raw == "none":
            return AdminCallback(action, ("none",))
        number = _require_int(data, raw, action)
        if isinstance(number, InvalidAdminCallback):
            return number
        return AdminCallback(action, (str(number),))

    if action == NOTIFY_TOGGLE:
        if len(rest) != 1 or not rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action in (NOTIFY_ALL, NOTIFY_PAUSE):
        if len(rest) != 1 or rest[0] not in ("on", "off"):
            return _invalid(data, "bad_switch", action)
        return AdminCallback(action, (rest[0],))

    if action == ASK:
        if len(rest) != 2 or not rest[0] or not rest[1]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0], rest[1]))

    if action == CANCEL:
        if len(rest) != 1 or not rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action in ACTION_KEYS:
        # Admin workspace actions always carry their subject (workspace id). The
        # panel has no subjectless action, so ``adm:resume`` is malformed.
        if len(rest) == 1 and rest[0]:
            return AdminCallback(action, (rest[0],))
        return _invalid(data, "bad_arity", action)

    if action == AUDIT_CAT:
        if len(rest) != 2 or not rest[0]:
            return _invalid(data, "bad_arity", action)
        page = _require_int(data, rest[1], action)
        if isinstance(page, InvalidAdminCallback):
            return page
        return AdminCallback(action, (rest[0], str(page)))

    if action == AUDIT_USER:
        if len(rest) != 2:
            return _invalid(data, "bad_arity", action)
        tg_id = _require_int(data, rest[0], action)
        if isinstance(tg_id, InvalidAdminCallback):
            return tg_id
        page = _require_int(data, rest[1], action)
        if isinstance(page, InvalidAdminCallback):
            return page
        return AdminCallback(action, (str(tg_id), str(page)))

    if action == WALLET:
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        tg_id = _require_int(data, rest[0], action)
        if isinstance(tg_id, InvalidAdminCallback):
            return tg_id
        return AdminCallback(action, (str(tg_id),))

    if action == WALLET_OP:
        # op, tg_id, step, optional arg
        if len(rest) not in (3, 4) or rest[2] not in ("ask", "go"):
            return _invalid(data, "bad_arity", action)
        tg_id = _require_int(data, rest[1], action)
        if isinstance(tg_id, InvalidAdminCallback):
            return tg_id
        arg = rest[3] if len(rest) == 4 else ""
        return AdminCallback(action, (rest[0], str(tg_id), rest[2], arg))

    if action == WALLET_PLAN:
        if len(rest) not in (4, 5) or rest[3] not in ("ask", "go"):
            return _invalid(data, "bad_arity", action)
        tg_id = _require_int(data, rest[1], action)
        if isinstance(tg_id, InvalidAdminCallback):
            return tg_id
        arg = rest[4] if len(rest) == 5 else ""
        return AdminCallback(action, (rest[0], str(tg_id), rest[2], rest[3], arg))

    if action == AUDIT_ALL:
        if len(rest) == 2 and rest[0] == "global":
            page = _require_int(data, rest[1], action)
            if isinstance(page, InvalidAdminCallback):
                return page
            return AdminCallback(action, ("global", str(page)))
        if len(rest) == 3 and rest[0] == "user":
            tg_id = _require_int(data, rest[1], action)
            if isinstance(tg_id, InvalidAdminCallback):
                return tg_id
            page = _require_int(data, rest[2], action)
            if isinstance(page, InvalidAdminCallback):
                return page
            return AdminCallback(action, ("user", str(tg_id), str(page)))
        return _invalid(data, "bad_scope", action)

    return _invalid(data, "unknown_action", action)
