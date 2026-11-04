"""The ``adm:`` callback codec: typed round-trips, a total parser, and the
structural contract that a screen can never produce a callback a handler cannot
parse (Task 07, Bug A).

Bug A was live: the per-user audit button was built with a bare f-string that
omitted the page, while the handler read ``parts[3]`` and raised ``IndexError``.
The button rendered nothing and the user saw nothing. These tests pin the fix and
the rule that prevents its whole class.
"""

from __future__ import annotations

import pytest

from cloudbot.presentation.keyboards.buttons import MAX_CALLBACK_BYTES
from cloudbot.presentation.screens.admin import callbacks as admin_cb
from cloudbot.presentation.screens.admin.callbacks import (
    AdminCallback,
    InvalidAdminCallback,
)

#: Every builder, with representative arguments. The contract test drives these
#: the same way a screen does.
BUILDERS = [
    ("menu", lambda: admin_cb.menu(), ("menu", ()), None),
    ("section", lambda: admin_cb.section("users"), ("sec", ("users",)), None),
    ("users_page", lambda: admin_cb.users_page(3), ("users_page", ("3",)), 3),
    ("users_search", lambda: admin_cb.users_search(), ("users_search", ()), None),
    ("user", lambda: admin_cb.user(6508129575), ("user", ("6508129575",)), 6508129575),
    ("ask", lambda: admin_cb.ask("suspend", "ws_x"), ("ask", ("suspend", "ws_x")), None),
    ("cancel", lambda: admin_cb.cancel("ws_x"), ("cancel", ("ws_x",)), None),
    ("action", lambda: admin_cb.action("enter", "ws_x"), ("enter", ("ws_x",)), None),
    ("leave", lambda: admin_cb.leave(), ("leave", ()), None),
    ("notify_toggle", lambda: admin_cb.notify_toggle("run_started"), ("notify_toggle", ("run_started",)), None),
    ("notify_all", lambda: admin_cb.notify_all(True), ("notify_all", ("on",)), None),
    ("notify_all_off", lambda: admin_cb.notify_all(False), ("notify_all", ("off",)), None),
    ("notify_pause", lambda: admin_cb.notify_pause(True), ("notify_pause", ("on",)), None),
    ("notify_scope", lambda: admin_cb.notify_scope(55), ("notify_scope", ("55",)), 55),
    ("notify_scope_none", lambda: admin_cb.notify_scope(None), ("notify_scope", ("none",)), None),
    ("audit_cat", lambda: admin_cb.audit_cat("run_started", 2), ("audit_cat", ("run_started", "2")), 2),
    ("audit_all_global", lambda: admin_cb.audit_all(None, 1), ("audit_all", ("global", "1")), 1),
    ("audit_all_user", lambda: admin_cb.audit_all(55, 4), ("audit_all", ("user", "55", "4")), 4),
    ("audit", lambda: admin_cb.audit(7), ("audit", ("7",)), 7),
    # The exact button from Bug A, now carrying its page.
    ("audit_user", lambda: admin_cb.audit_user(6508129575, 0), ("audit_user", ("6508129575", "0")), 0),
    ("audit_user_paged", lambda: admin_cb.audit_user(6508129575, 5), ("audit_user", ("6508129575", "5")), 5),
]


@pytest.mark.parametrize("name,builder,expected,trailing_int", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_builder_round_trips(name, builder, expected, trailing_int):
    data = builder()
    assert data.startswith("adm:")
    assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES, data
    parsed = admin_cb.parse(data)
    assert isinstance(parsed, AdminCallback), parsed
    assert parsed.valid is True
    assert (parsed.action, parsed.args) == expected


def test_audit_user_always_carries_a_page():
    """The regression that was live: the button must carry the page the handler
    reads. Two components after the action — never one."""
    data = admin_cb.audit_user(6508129575, 0)
    assert data == "adm:audit_user:6508129575:0"
    parsed = admin_cb.parse(data)
    assert parsed.valid
    assert parsed.arg_int(0) == 6508129575
    assert parsed.arg_int(1) == 0


# -- the total, defensive parser ---------------------------------------------
MALFORMED = [
    "",  # empty
    "adm",  # no separator
    "adm:",  # no action
    "adm:audit_user",  # missing every arg (the shape that used to IndexError)
    "adm:audit_user:6508129575",  # the exact short callback from Bug A
    "adm:audit_user:notanumber:0",
    "adm:audit_user:6508129575:notanumber",
    "adm:sec",  # section without a key
    "adm:sec:a:b",  # too many args
    "adm:users_page:notanumber",
    "adm:notify_all:maybe",
    "adm:notify_scope:notanumber",
    "adm:audit_all:weird:0",
    "adm:audit_all:user:notanumber:0",
    "adm:menu:extra",
    "adm:ask:onlyone",
    "adm:cancel",  # cancel without a subject
    "adm:enter",  # an action with no subject and no bare form
    "adm:totally_unknown",
    "file:ls:ws_x:0",  # not an admin callback at all
    "nav:menu",
]


@pytest.mark.parametrize("data", MALFORMED, ids=[repr(d) for d in MALFORMED])
def test_malformed_callbacks_return_invalid_not_raise(data):
    parsed = admin_cb.parse(data)
    assert isinstance(parsed, InvalidAdminCallback), parsed
    assert parsed.valid is False
    assert parsed.raw == data


def test_parse_never_raises_for_arbitrary_text():
    for text in ("", ":", "::::", "adm:::::", "🛠", "adm:sec:" + "x" * 400):
        parsed = admin_cb.parse(text)
        # Either a valid callback or the invalid sentinel — never an exception.
        assert parsed.valid is True or isinstance(parsed, InvalidAdminCallback)
