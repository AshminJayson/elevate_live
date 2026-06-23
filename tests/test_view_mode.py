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
from starlette.websockets import WebSocketDisconnect, WebSocketState

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


def _host_endpoint(app):
    """Return the bare `host` websocket handler registered at /ws/host."""
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/ws/host")


def test_host_loop_exits_cleanly_when_disconnect_consumed_by_send(tmp_path):
    """A host socket reaped mid-fan-out must exit the loop cleanly, not crash.

    Reproduces the production trace: a host's cycle_view_mode triggers a fan-out
    (advance_view_mode) whose send to that same socket finds the client gone and
    reaps it, flipping application_state to DISCONNECTED. The handler must stop
    rather than call receive_json on the dead socket — which raises RuntimeError
    ('WebSocket is not connected'), not WebSocketDisconnect, and would otherwise
    escape as an unhandled ASGI exception.

    The fake models Starlette faithfully so the test fails if the state-guard is
    removed: receive_json on a non-CONNECTED socket raises the same RuntimeError,
    and send on a gone socket flips the state and raises WebSocketDisconnect.
    """

    class _ReapingWS:
        """Fake host socket tracking application_state like Starlette's WebSocket.

        send_json records payloads while live; once the client is gone it flips
        application_state to DISCONNECTED and raises WebSocketDisconnect (the
        fan-out reaping this socket). receive_json returns one cycle_view_mode
        control then marks the client gone; called again on a DISCONNECTED socket
        it raises the "not connected" RuntimeError, matching Starlette.
        """

        def __init__(self):
            self.sent = []
            self.application_state = WebSocketState.CONNECTED
            self._client_gone = False

        async def accept(self):
            pass

        async def send_json(self, message):
            if self._client_gone:
                self.application_state = WebSocketState.DISCONNECTED
                raise WebSocketDisconnect(code=1006)
            self.sent.append(message)

        async def receive_json(self):
            if self.application_state != WebSocketState.CONNECTED:
                raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')
            self._client_gone = True  # client departs right after sending this
            return {"type": "control", "action": "cycle_view_mode"}

    app = create_app(_config(tmp_path))
    ws = _ReapingWS()
    # Must not raise: the loop re-checks application_state and exits cleanly.
    asyncio.run(_host_endpoint(app)(ws, token="secret"))
    # The cycle was processed even though the echo to the now-dead host failed.
    assert app.state.live.view_mode == "code"


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
