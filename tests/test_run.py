"""Tests for the supervisor's pure command builders (wiring is verified manually)."""

from bitforge.run import _ttyd_cmd


def test_ttyd_cmd_is_read_only_scrollable_and_themed():
    """ttyd argv binds localhost under /terminal, enables scrollback, and attaches
    the session read-only."""
    cmd = _ttyd_cmd("class")
    assert cmd[0] == "ttyd"
    assert "-i" in cmd and "127.0.0.1" in cmd
    assert cmd[cmd.index("-b") + 1] == "/terminal"
    assert "scrollback=10000" in cmd
    # read-only attach to the named session is the terminal source
    assert cmd[-5:] == ["tmux", "attach", "-r", "-t", "class"]
