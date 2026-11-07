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
# -- moderation: user/workspace bans + messaging a user ----------------------
USER_BAN = "user_ban"
USER_UNBAN = "user_unban"
USER_MSG = "user_msg"
BAN_GO = "ban_go"
WS_BAN = "ws_ban"
WS_UNBAN = "ws_unban"
# -- wallet + subscription controls -----------------------------------------
WALLET = "wallet"
WALLET_OP = "wop"
WALLET_PLAN = "wplan"
# -- force-join gate ---------------------------------------------------------
FJ_ADD = "fj_add"
FJ_TOGGLE = "fj_toggle"
FJ_REMOVE = "fj_remove"
FJ_SWITCH = "fj_switch"
FJ_CHECK = "fj_check"
# -- backup --------------------------------------------------------------
BK_SCHEDULE = "bk_sched"
BK_INSTANT = "bk_now"
BK_SWITCH = "bk_switch"
BK_HOURS = "bk_hours"
BK_DAILY = "bk_daily"
BK_RETENTION = "bk_keep"
BK_DEST = "bk_dest"
BK_PRUNE = "bk_prune"
BK_RESTORE = "bk_restore"
BK_DISK = "bk_disk"
BK_GO = "bk_go"
BK_CANCEL = "bk_cancel"
# -- broadcast ---------------------------------------------------------------
BC_COMPOSE = "bc_compose"
BC_AUDIENCE = "bc_aud"
BC_CONFIRM = "bc_go"
BC_RESULT = "bc_result"
BC_RETRY = "bc_retry"
BC_HISTORY = "bc_hist"
BC_RESEND = "bc_resend"
BC_CANCEL = "bc_cancel"

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

# -- moderation --------------------------------------------------------------
def user_ban(tg_id: int) -> str:
    """Open the ban-reason prompt for a user."""
    return _action(USER_BAN, int(tg_id))

def user_unban(tg_id: int) -> str:
    """One tap: lift a user ban."""
    return _action(USER_UNBAN, int(tg_id))

def user_msg(tg_id: int) -> str:
    """Open the "send this user a message" prompt."""
    return _action(USER_MSG, int(tg_id))

def ban_go(kind: str, subject: str, days: int = 0) -> str:
    """Perform the ban whose reason is held in FSM state. ``days`` 0 is permanent."""
    return _action(BAN_GO, kind, subject, int(days))

def ws_ban(ws_id: str) -> str:
    return _action(WS_BAN, ws_id)

def ws_unban(ws_id: str) -> str:
    return _action(WS_UNBAN, ws_id)

# -- force-join gate ---------------------------------------------------------
def forcejoin_add() -> str:
    return _action(FJ_ADD)

def forcejoin_toggle(chat_id: int) -> str:
    return _action(FJ_TOGGLE, int(chat_id))

def forcejoin_remove(chat_id: int) -> str:
    return _action(FJ_REMOVE, int(chat_id))

def forcejoin_switch(enabled: bool) -> str:
    return _action(FJ_SWITCH, "on" if enabled else "off")

def forcejoin_check() -> str:
    return _action(FJ_CHECK)


# -- backup ------------------------------------------------------------------
def backup_schedule() -> str:
    return _action(BK_SCHEDULE)

def backup_instant(scope: str = "full") -> str:
    return _action(BK_INSTANT, "settings" if scope == "settings" else "full")

def backup_switch(enabled: bool) -> str:
    return _action(BK_SWITCH, "on" if enabled else "off")

def backup_hours(hours: int) -> str:
    return _action(BK_HOURS, int(hours))

def backup_daily(time: str) -> str:
    """``time`` is ``HH:MM``; it is sent without the colon (the wire is colon-split)."""
    return _action(BK_DAILY, str(time).replace(":", ""))

def backup_retention(keep: int) -> str:
    return _action(BK_RETENTION, int(keep))

def backup_destination(tg_id: int) -> str:
    return _action(BK_DEST, int(tg_id))

def backup_prune() -> str:
    return _action(BK_PRUNE)

def backup_restore() -> str:
    return _action(BK_RESTORE)

def backup_disk(name: str) -> str:
    return _action(BK_DISK, name)

def backup_go(token: str, scope: str) -> str:
    return _action(BK_GO, token, "settings" if scope == "settings" else "full")

def backup_cancel(token: str) -> str:
    return _action(BK_CANCEL, token)

# -- broadcast ---------------------------------------------------------------
def broadcast_compose() -> str:
    return _action(BC_COMPOSE)

def broadcast_audience(audience: str) -> str:
    return _action(BC_AUDIENCE, audience)

def broadcast_confirm(audience: str, over_ceiling: bool = False) -> str:
    """Confirm the send. ``over_ceiling`` is the explicit bigger-number consent."""
    return _action(BC_CONFIRM, audience, "1" if over_ceiling else "0")

def broadcast_result(run_id: int) -> str:
    return _action(BC_RESULT, int(run_id))

def broadcast_retry(run_id: int) -> str:
    return _action(BC_RETRY, int(run_id))

def broadcast_history() -> str:
    return _action(BC_HISTORY)

def broadcast_resend(run_id: int) -> str:
    return _action(BC_RESEND, int(run_id))

def broadcast_cancel() -> str:
    return _action(BC_CANCEL)

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

    if action in (USER_BAN, USER_UNBAN, USER_MSG):
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        tg_id = _require_int(data, rest[0], action)
        if isinstance(tg_id, InvalidAdminCallback):
            return tg_id
        return AdminCallback(action, (str(tg_id),))

    if action in (WS_BAN, WS_UNBAN):
        if len(rest) != 1 or not rest[0] or ":" in rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action == BAN_GO:
        if len(rest) != 3 or rest[0] not in ("user", "ws") or not rest[1]:
            return _invalid(data, "bad_arity", action)
        days = _require_int(data, rest[2], action)
        if isinstance(days, InvalidAdminCallback):
            return days
        if days < 0:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0], rest[1], str(days)))

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

    if action in (FJ_ADD, FJ_CHECK):
        if rest:
            return _invalid(data, "too_many_args", action)
        return AdminCallback(action)

    if action in (FJ_TOGGLE, FJ_REMOVE):
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        chat_id = _require_int(data, rest[0], action)
        if isinstance(chat_id, InvalidAdminCallback):
            return chat_id
        return AdminCallback(action, (str(chat_id),))

    if action == FJ_SWITCH:
        if len(rest) != 1 or rest[0] not in ("on", "off"):
            return _invalid(data, "bad_switch", action)
        return AdminCallback(action, (rest[0],))

    if action == BK_SCHEDULE or action == BK_PRUNE or action == BK_RESTORE:
        if rest:
            return _invalid(data, "too_many_args", action)
        return AdminCallback(action)

    if action == BK_INSTANT:
        if len(rest) != 1 or rest[0] not in ("full", "settings"):
            return _invalid(data, "bad_switch", action)
        return AdminCallback(action, (rest[0],))

    if action == BK_SWITCH:
        if len(rest) != 1 or rest[0] not in ("on", "off"):
            return _invalid(data, "bad_switch", action)
        return AdminCallback(action, (rest[0],))

    if action in (BK_HOURS, BK_RETENTION, BK_DEST):
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        number = _require_int(data, rest[0], action)
        if isinstance(number, InvalidAdminCallback):
            return number
        return AdminCallback(action, (str(number),))

    if action == BK_DAILY:
        if len(rest) != 1 or not rest[0].isdigit() or len(rest[0]) != 4:
            return _invalid(data, "bad_arity", action)
        raw = rest[0]
        return AdminCallback(action, (f"{raw[:2]}:{raw[2:]}",))

    if action == BK_DISK:
        # A filename never contains a colon; the codec refuses one anyway so a
        # crafted callback cannot smuggle a path separator through this segment.
        if len(rest) != 1 or not rest[0] or ":" in rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action == BK_GO:
        if len(rest) != 2 or not rest[0] or rest[1] not in ("full", "settings"):
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0], rest[1]))

    if action == BK_CANCEL:
        if len(rest) != 1 or not rest[0]:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (rest[0],))

    if action == BC_COMPOSE or action == BC_HISTORY or action == BC_CANCEL:
        if rest:
            return _invalid(data, "too_many_args", action)
        return AdminCallback(action)

    if action == BC_AUDIENCE:
        if len(rest) != 1 or rest[0] not in ("all", "active", "expired", "no_plan"):
            return _invalid(data, "bad_scope", action)
        return AdminCallback(action, (rest[0],))

    if action == BC_CONFIRM:
        if len(rest) != 2 or rest[0] not in ("all", "active", "expired", "no_plan"):
            return _invalid(data, "bad_arity", action)
        if rest[1] not in ("0", "1"):
            return _invalid(data, "bad_switch", action)
        return AdminCallback(action, (rest[0], rest[1]))

    if action in (BC_RESULT, BC_RETRY, BC_RESEND):
        if len(rest) != 1:
            return _invalid(data, "bad_arity", action)
        run_id = _require_int(data, rest[0], action)
        if isinstance(run_id, InvalidAdminCallback):
            return run_id
        if run_id < 0:
            return _invalid(data, "bad_arity", action)
        return AdminCallback(action, (str(run_id),))

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
