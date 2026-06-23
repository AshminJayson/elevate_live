"""Tests for the host-controlled view-mode cycle (free / code / terminal).

The cross-connection fan-out (a host control message reaching a separate viewer
socket) is NOT exercised here: Starlette's TestClient uses a separate event-loop
portal per websocket, so live cross-socket delivery is undelivered (see the
_CROSS_PORTAL note in test_server_broadcast). Instead this verifies the two
pieces that work in-process:
  - a connecting viewer's initial state carries the current view mode, and
  - advance_view_mode cycles the mode and notifies viewer + host sockets,
exercised at the function level with dummy sockets.
"""

import asyncio

from fastapi.testclient import TestClient

from bitforge.config import Settings
from bitforge.server import State, advance_view_mode, create_app


def _config(tmp_path):
    return Settings(_env_file=None, source_dir=tmp_path, token="secret")


def test_viewer_initial_state_includes_view_mode(tmp_path):
    """A joining viewer receives the current view mode after the tree (free by default)."""
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/viewer") as viewer:
        assert viewer.receive_json()["type"] == "tree"  # initial tree (no file yet)
        assert viewer.receive_json() == {"type": "view_mode", "mode": "free"}


class _DummyWS:
    """Minimal stand-in for a WebSocket that records send_json payloads.

    Schema of `sent`: list[dict] — each the wire message passed to send_json.
    """

    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def test_advance_view_mode_cycles_and_notifies():
    """advance_view_mode cycles free->code->terminal->free and notifies viewers + hosts."""
    state = State()
    viewer = _DummyWS()
    host = _DummyWS()
    state.viewers.add(viewer)
    state.hosts.add(host)

    assert state.view_mode == "free"
    asyncio.run(advance_view_mode(state))
    assert state.view_mode == "code"
    asyncio.run(advance_view_mode(state))
    assert state.view_mode == "terminal"
    asyncio.run(advance_view_mode(state))
    assert state.view_mode == "free"  # wraps back

    expected = [
        {"type": "view_mode", "mode": "code"},
        {"type": "view_mode", "mode": "terminal"},
        {"type": "view_mode", "mode": "free"},
    ]
    assert viewer.sent == expected
    assert host.sent == expected  # the host gets the same echo for its status bar
