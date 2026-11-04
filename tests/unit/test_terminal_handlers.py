"""Terminal handler wiring: commands are never swallowed as terminal input."""

from __future__ import annotations

from cloudbot.handlers.terminal import _is_command, _parse_size


def test_is_command_detection():
    # A slash-command is never swallowed as terminal input (D5).
    assert _is_command("/help")
    assert _is_command("/settings arg")
    assert not _is_command("ls -la")
    assert not _is_command("echo /etc/hosts")
    assert not _is_command("")


def test_parse_size_accepts_documented_forms():
    assert _parse_size("120x40") == (120, 40)
    assert _parse_size("100×30") == (100, 30)
    assert _parse_size(" 80 x 24 ") == (80, 24)
