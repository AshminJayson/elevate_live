"""FastAPI broadcast hub: state, teacher auth, student fan-out.

Includes the /terminal proxy (WebSocket and HTTP) and the GET / student page.
"""

import asyncio
import os
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from liveclass.config import Config, load_config

# ttyd runs with base path -b /terminal (see run.py), so it serves its page,
# token, and websocket under /terminal/. The proxy must preserve that prefix.
TTYD_HTTP = "http://127.0.0.1:7681/terminal"
TTYD_WS = "ws://127.0.0.1:7681/terminal/ws"
_STATIC = Path(__file__).resolve().parent.parent / "static"


class State:
    """In-memory broadcast state — the single source of truth for late-joiners.

    Attributes:
        current_tree (list): latest tree nodes (see tree.build_tree schema).
        current_file (dict | None): latest 'file' wire message, or None.
        students (set[WebSocket]): connected student sockets.
    """

    def __init__(self):
        """Initialize empty broadcast state (no tree, no file, no students)."""
        self.current_tree = []
        self.current_file = None
        self.students = set()


async def _broadcast(state, message):
    """Send a JSON message to every connected student, dropping dead sockets.

    Args:
        state (State): the shared state.
        message (dict): a wire message (file or tree).
    """
    dead = []
    for ws in list(state.students):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.students.discard(ws)


def create_app(config=None):
    """Build the FastAPI app with broadcast routes.

    Args:
        config (Config | None): configuration; if None, load from the path in
            LIVECLASS_CONFIG (default "liveclass.toml").

    Returns:
        FastAPI: the configured application.
    """
    if config is None:
        config = load_config(os.environ.get("LIVECLASS_CONFIG", "liveclass.toml"))

    app = FastAPI()
    app.state.config = config
    app.state.live = State()

    @app.websocket("/ws/teacher")
    async def teacher(ws: WebSocket, token: str = ""):
        """Authenticate teacher and broadcast state updates to all students.

        Validates token (rejects on empty config.token or mismatch with 1008),
        then loops receiving JSON, updating State (tree/file) and broadcasting
        to students, removing dead sockets, exiting cleanly on disconnect.

        Args:
            ws (WebSocket): the teacher connection.
            token (str): query parameter token to validate against config.token.
        """
        if not config.token or token != config.token:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        state = app.state.live
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "tree":
                    state.current_tree = message.get("tree", [])
                elif message.get("type") == "file":
                    state.current_file = message
                await _broadcast(state, message)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/student")
    async def student(ws: WebSocket):
        """Accept student, send current state, then drain receive-only until disconnect.

        Accepts, registers the socket, sends current tree then current file
        (late-joiner state), then drains receive-only until disconnect,
        discarding the socket in finally.

        Args:
            ws (WebSocket): the student connection.
        """
        await ws.accept()
        state = app.state.live
        state.students.add(ws)
        await ws.send_json({"type": "tree", "tree": state.current_tree})
        if state.current_file is not None:
            await ws.send_json(state.current_file)
        try:
            while True:
                await ws.receive_text()  # students send nothing; this detects disconnect
        except WebSocketDisconnect:
            pass
        finally:
            state.students.discard(ws)

    @app.get("/file", response_class=PlainTextResponse)
    async def file(path: str):
        """Serve a single lesson file as plain text, sandboxed and ignore-aware.

        Algorithm:
            1. Reload the ignore list from disk so hot-edits to liveclass.toml
               take effect immediately (enforcement never depends on a message).
            2. Resolve lesson_dir/path; reject (404) if it escapes lesson_dir.
            3. Reject (404) if missing, a directory, or ignored.
            4. Return the file text.

        Args:
            path (str): POSIX-relative path under lesson_dir.

        Returns:
            PlainTextResponse: file content, or 404 on any rejection.
        """
        from liveclass.config import is_ignored

        fresh = load_config(os.environ.get("LIVECLASS_CONFIG", "liveclass.toml"), token=config.token)
        ignore = fresh.ignore
        base = config.lesson_dir.resolve()
        target = (base / path).resolve()
        rel = os.path.relpath(target, base)
        if rel.startswith("..") or os.path.isabs(rel):
            return PlainTextResponse("not found", status_code=404)
        if not target.is_file() or is_ignored(Path(rel).as_posix(), ignore):
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(target.read_text())

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the student page HTML (static/index.html)."""
        return HTMLResponse((_STATIC / "index.html").read_text())

    @app.websocket("/terminal/ws")
    async def terminal_ws(ws: WebSocket):
        """Reverse-proxy the student terminal WebSocket to ttyd (read-only).

        Pumps frames both directions between the student and ttyd, preserving
        ttyd's 'tty' subprotocol. ttyd itself enforces read-only (no -W,
        tmux attach -r), so nothing students send can affect the host.
        """
        await ws.accept(subprotocol="tty")
        try:
            async with websockets.connect(TTYD_WS, subprotocols=["tty"], open_timeout=5) as upstream:

                async def to_upstream():
                    while True:
                        data = await ws.receive_bytes()
                        await upstream.send(data)

                async def to_client():
                    async for data in upstream:
                        if isinstance(data, str):
                            await ws.send_text(data)
                        else:
                            await ws.send_bytes(data)

                done, pending = await asyncio.wait(
                    [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except (OSError, websockets.exceptions.WebSocketException):
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)

    @app.get("/terminal/{path:path}")
    async def terminal_http(path: str, request: Request):
        """Reverse-proxy ttyd's HTTP assets (its xterm.js page) under /terminal/.

        Args:
            path (str): the ttyd asset path (e.g. "index.html", "js/xterm.js").
            request (Request): the incoming request; query params are forwarded to ttyd.

        Returns:
            Response: proxied response with ttyd's status code and content-type,
                or 502 if ttyd is unreachable.
        """
        async with httpx.AsyncClient() as http:
            try:
                upstream = await http.get(f"{TTYD_HTTP}/{path}", params=request.query_params)
            except httpx.RequestError:
                return Response(content=b"terminal unavailable", status_code=502)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
