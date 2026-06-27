import asyncio
import json
import time

import pytest
import websockets
from fastapi.testclient import TestClient

from bitforge.config import Settings
from bitforge.server import _sanitize_cursor_message, create_app


async def _recv_until(ws, msg_type, timeout=2.0):
    """Receive JSON messages from ws until one has type == msg_type; return it.

    Skips initial-state and echo frames (tree, view_mode) so a test can assert on
    the specific broadcast it cares about, independent of the initial burst order.

    Args:
        ws: an open websockets client connection.
        msg_type (str): the "type" value to wait for.
        timeout (float): max seconds to wait for each individual frame.

    Returns:
        dict: the first decoded message whose "type" equals msg_type.

    Raises:
        asyncio.TimeoutError: if no frame arrives within `timeout` (fail fast
            rather than hang the suite).
    """
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        msg = json.loads(raw)
        if msg.get("type") == msg_type:
            return msg


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """Poll predicate until it is truthy or the timeout elapses; return its final value.

    TestClient runs the ASGI app on a separate event-loop thread per websocket,
    so a message a host just sent is processed asynchronously — the main test
    thread must wait for the server to apply it before asserting on shared state.

    Args:
        predicate (Callable[[], bool]): condition to poll (e.g. state is stored).
        timeout (float): max seconds to wait.
        interval (float): seconds slept between polls (yields the GIL so the
            server thread can run).

    Returns:
        bool: the predicate's final truthiness.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _config(tmp_path, token="secret"):
    return Settings(
        _env_file=None,
        source_dir=tmp_path,
        title="Test",
        ignore=[],
        tmux_session="class",
        cols=100,
        rows=30,
        token=token,
    )


def test_host_bad_token_rejected(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/host?token=wrong"):
            pass


def test_host_empty_config_token_rejected(tmp_path):
    app = create_app(_config(tmp_path, token=""))
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/host?token="):
            pass


async def test_host_broadcast_reaches_viewer(tmp_path, live_server):
    """A host 'file' fans out to a viewer on a separate live connection."""
    base = live_server(create_app(_config(tmp_path)))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")  # drain initial state; viewer now registered
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps(
                {"type": "file", "path": "main.py", "language": "python", "content": "x=1"}))
            msg = await _recv_until(viewer, "file")
            assert msg == {"type": "file", "path": "main.py", "language": "python", "content": "x=1"}


async def test_host_file_language_derived_server_side(tmp_path, live_server):
    """A host 'file' with no language field gets one derived from its path."""
    base = live_server(create_app(_config(tmp_path)))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps({"type": "file", "path": "main.py", "content": "x=1"}))
            msg = await _recv_until(viewer, "file")
            assert msg == {"type": "file", "path": "main.py", "language": "python", "content": "x=1"}


async def test_host_file_path_traversal_dropped(tmp_path, live_server):
    """A 'file' whose path escapes source_dir is dropped (never broadcast)."""
    base = live_server(create_app(_config(tmp_path)))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps({"type": "file", "path": "../secret.py", "content": "leak"}))
            await host.send(json.dumps({"type": "file", "path": "ok.py", "content": "fine"}))
            # the traversal message was dropped; the first file the viewer sees is ok.py
            msg = await _recv_until(viewer, "file")
            assert msg["path"] == "ok.py" and msg["content"] == "fine"


async def test_host_file_ignored_path_dropped(tmp_path, live_server):
    """A 'file' matching the configured ignore list is dropped (never broadcast)."""
    config = _config(tmp_path)
    config.ignore = ["*.pyc"]
    base = live_server(create_app(config))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps({"type": "file", "path": "a.pyc", "content": "ignored"}))
            await host.send(json.dumps({"type": "file", "path": "b.py", "content": "shown"}))
            msg = await _recv_until(viewer, "file")
            assert msg["path"] == "b.py" and msg["content"] == "shown"


async def test_host_cursor_reaches_viewer(tmp_path, live_server):
    """A host 'cursor' fans out to a viewer with coordinates coerced to ints."""
    base = live_server(create_app(_config(tmp_path)))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps(
                {"type": "cursor", "path": "main.py", "line": 4, "column": 2,
                 "anchorLine": 4, "anchorColumn": 6}))
            msg = await _recv_until(viewer, "cursor")
            assert msg == {"type": "cursor", "path": "main.py", "line": 4, "column": 2,
                           "anchorLine": 4, "anchorColumn": 6}


async def test_host_cursor_path_traversal_dropped(tmp_path, live_server):
    """A 'cursor' whose path escapes source_dir is dropped (never broadcast)."""
    base = live_server(create_app(_config(tmp_path)))
    async with websockets.connect(f"{base}/ws/viewer") as viewer:
        await _recv_until(viewer, "tree")
        async with websockets.connect(f"{base}/ws/host?token=secret") as host:
            await host.send(json.dumps({"type": "cursor", "path": "../escape.py", "line": 0, "column": 0,
                                        "anchorLine": 0, "anchorColumn": 0}))
            await host.send(json.dumps({"type": "cursor", "path": "ok.py", "line": 1, "column": 1,
                                        "anchorLine": 1, "anchorColumn": 1}))
            # the traversal cursor was dropped; the first cursor the viewer sees is ok.py
            msg = await _recv_until(viewer, "cursor")
            assert msg["path"] == "ok.py"


def test_sanitize_cursor_coerces_and_clamps(tmp_path):
    """Non-int / negative coordinates are coerced to non-negative ints; path checked."""
    config = _config(tmp_path)
    msg = _sanitize_cursor_message(
        config,
        {"type": "cursor", "path": "main.py", "line": "5", "column": -3,
         "anchorLine": None, "anchorColumn": 2},
        config.ignore,
    )
    assert msg == {"type": "cursor", "path": "main.py", "line": 5, "column": 0,
                   "anchorLine": 0, "anchorColumn": 2}
    # path escaping the sandbox is rejected outright
    assert _sanitize_cursor_message(config, {"path": "../x.py"}, config.ignore) is None


def test_late_joiner_gets_current_cursor(tmp_path):
    """A viewer joining after a cursor was set receives it in the initial burst."""
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/host?token=secret") as host:
        host.send_json({"type": "file", "path": "main.py", "content": "x=1"})
        host.send_json({"type": "cursor", "path": "main.py", "line": 0, "column": 1,
                        "anchorLine": 0, "anchorColumn": 1})
        assert _wait_until(lambda: app.state.live.current_cursor is not None)
        with client.websocket_connect("/ws/viewer") as viewer:
            got_tree = viewer.receive_json()
            got_file = viewer.receive_json()
            got_cursor = viewer.receive_json()
            assert got_tree["type"] == "tree"
            assert got_file["type"] == "file"
            assert got_cursor == {"type": "cursor", "path": "main.py", "line": 0, "column": 1,
                                  "anchorLine": 0, "anchorColumn": 1}


def test_switching_file_resets_cursor(tmp_path):
    """Switching the live file to a new path drops the stale stored cursor."""
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/host?token=secret") as host:
        host.send_json({"type": "file", "path": "a.py", "content": "1"})
        host.send_json({"type": "cursor", "path": "a.py", "line": 2, "column": 0,
                        "anchorLine": 2, "anchorColumn": 0})
        assert _wait_until(lambda: app.state.live.current_cursor is not None)
        host.send_json({"type": "file", "path": "b.py", "content": "2"})
        # the caret belonged to a.py; switching to b.py invalidates it
        assert _wait_until(lambda: app.state.live.current_cursor is None)


def test_viewer_connect_tracks_peak_online(tmp_path):
    """A connecting viewer is counted; peak_online records the high-water mark."""
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    assert app.state.live.peak_online == 0
    with client.websocket_connect("/ws/viewer") as viewer:
        viewer.receive_json()  # drain initial tree
        assert len(app.state.live.viewers) == 1
        assert app.state.live.peak_online == 1
    # peak survives the disconnect even though the live count drops back to 0
    assert len(app.state.live.viewers) == 0
    assert app.state.live.peak_online == 1


def test_late_joiner_gets_current_state(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/host?token=secret") as host:
        host.send_json({"type": "tree", "tree": [{"name": "main.py", "path": "main.py", "type": "file"}]})
        host.send_json({"type": "file", "path": "main.py", "language": "python", "content": "x=1"})
        # Wait until the server has actually stored the file before a viewer
        # joins, so the late-joiner state burst is deterministic: the viewer
        # then receives tree, file, view_mode in that order.
        assert _wait_until(lambda: app.state.live.current_file is not None)
        with client.websocket_connect("/ws/viewer") as viewer:
            got_tree = viewer.receive_json()
            got_file = viewer.receive_json()
            assert got_tree["type"] == "tree"
            assert got_tree["tree"][0]["path"] == "main.py"
            assert got_file["type"] == "file"
            assert got_file["content"] == "x=1"
