"""Shared pytest fixtures for the BitForge test suite.

Provides `live_server`, a factory that boots a real uvicorn server in a daemon
thread. This exists because Starlette's TestClient opens a separate event-loop
portal per websocket connection, so a broadcast sent from one connection never
reaches another — defeating any test of live host->viewer fan-out. A single real
uvicorn server handles both connections on one event loop, exercising the genuine
broadcast path.
"""

import socket
import threading
import time

import pytest
import uvicorn


class _ThreadedUvicorn(uvicorn.Server):
    """A uvicorn Server that runs off the main thread (no signal handlers).

    uvicorn installs SIGINT/SIGTERM handlers in run(), which only works on the
    main thread; tests drive shutdown via should_exit instead, so this is a no-op.
    """

    def install_signal_handlers(self):
        pass


@pytest.fixture
def live_server():
    """Factory fixture: start a real uvicorn server for an ASGI app; yield its base URL.

    Algorithm:
        1. Return a `start(app)` callable. On call: bind a socket to 127.0.0.1:0
           (kernel-assigned free port), read back the port, and hand the socket to
           a _ThreadedUvicorn running in a daemon thread. Passing the already-bound
           socket avoids the bind/close race of picking a port then reopening it.
        2. Poll server.started (<=5s) so the caller only gets the URL once the
           server accepts connections.
        3. Track every (server, thread, socket) so teardown sets should_exit,
           joins the thread, and closes the socket.

    Returns:
        Callable[[ASGIApp], str]: start(app) -> "ws://127.0.0.1:<port>".
    """
    started = []

    def start(app):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = _ThreadedUvicorn(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("uvicorn did not start within 5s")
        started.append((server, thread, sock))
        return f"ws://127.0.0.1:{port}"

    yield start

    for server, thread, sock in started:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
