"""FastAPI broadcast hub: state, teacher auth, student fan-out.

Later tasks add the /terminal proxy and GET / page.
"""

import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse

from liveclass.config import Config, load_config


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

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
