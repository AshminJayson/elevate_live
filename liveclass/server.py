"""FastAPI broadcast hub: state, teacher auth, student fan-out.

Later tasks add the /file endpoint, the /terminal proxy, and GET / page.
"""

import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from liveclass.config import Config, load_config


class State:
    """In-memory broadcast state — the single source of truth for late-joiners.

    Attributes:
        current_tree (list): latest tree nodes (see tree.build_tree schema).
        current_file (dict | None): latest 'file' wire message, or None.
        students (set[WebSocket]): connected student sockets.
    """

    def __init__(self):
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

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
