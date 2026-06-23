"""FastAPI broadcast hub: state, host auth, viewer fan-out.

Includes the /terminal proxy (WebSocket and HTTP) and the GET / viewer page.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from bitforge.config import is_ignored, load_settings
from bitforge.protocol import file_message

# Two audiences (see bitforge.logging_setup): _console carries the periodic
# heartbeat to the host's terminal (and the file); _events records per-
# connection detail to the file only, keeping the console quiet.
_console = logging.getLogger("bitforge.console")
_events = logging.getLogger("bitforge.events")

# ttyd runs with base path -b /terminal (see run.py), so it serves its page,
# token, and websocket under /terminal/. The proxy must preserve that prefix.
TTYD_HTTP = "http://127.0.0.1:7681/terminal"
TTYD_WS = "ws://127.0.0.1:7681/terminal/ws"
_STATIC = Path(__file__).resolve().parent.parent / "static"

# Host-controlled viewer layout, cycled in order by either trigger (the host's
# terminal hotkey or the extension command). "free" returns control to each
# viewer; "code"/"terminal" force the layout and lock the viewer's toggle.
VIEW_MODES = ("free", "code", "terminal")


class State:
    """In-memory broadcast state — the single source of truth for late-joiners.

    Attributes:
        current_tree (list): latest tree nodes (see tree.build_tree schema).
        current_root (str): display name of the broadcast project root (source
            dir basename), shown as the explorer heading; "" until first tree.
        current_file (dict | None): latest 'file' wire message, or None.
        viewers (set[WebSocket]): connected viewer sockets (code/explorer
            viewers on /ws/viewer); len() is the live "viewers online" count.
        hosts (set[WebSocket]): connected host sockets (/ws/host); the view-mode
            echo is pushed here so an extension status bar tracks the live mode.
        view_mode (str): host-controlled layout forced on viewers, one of
            VIEW_MODES ("free" | "code" | "terminal"). "free" lets each viewer
            choose; "code"/"terminal" force the layout and lock the toggle.
        terminals (int): live count of read-only terminal viewers on
            /terminal/ws. Tracked for the file log; not shown on the heartbeat.
        peak_online (int): high-water mark of len(viewers) over the session,
            reported on the console heartbeat.
        started_at (float): time.monotonic() at construction, for uptime.
    """

    def __init__(self):
        """Initialize empty broadcast state (no tree, no file, no viewers)."""
        self.current_tree = []
        self.current_root = ""
        self.current_file = None
        self.viewers = set()
        self.hosts = set()
        self.view_mode = "free"
        self.terminals = 0
        self.peak_online = 0
        self.started_at = time.monotonic()


def _sanitize_file_message(config, message, ignore):
    """Validate and normalize an incoming host 'file' message; None if rejected.

    The host channel is multi-client (the filesystem broadcaster and the VS
    Code extension both push here), so the server is the single source of truth
    for path safety and language — never trusting a client to have filtered.

    Algorithm:
        1. Read message["path"]; reject if it is not a string.
        2. Resolve source_dir/path and reject any path that escapes source_dir
           (path traversal), mirroring the /file endpoint. Existence is NOT
           required: the buffer may be unsaved, so the file need not be on disk.
        3. Reject paths matching the ignore list (captured once per connection by
           the caller; see the host handler).
        4. Return a freshly built file message via protocol.file_message, which
           re-derives 'language' from the sandbox-relative path — so clients need
           not send (or be trusted for) the language field.

    Args:
        config (Settings): resolved configuration (for source_dir).
        message (dict): incoming wire message; expects "path" (str) and
            "content" (str).
        ignore (list[str]): ignore patterns to enforce.

    Returns:
        dict | None: a normalized {"type":"file","path","language","content"}
            message, or None if the path is missing, escapes source_dir, or is
            ignored.
    """
    path = message.get("path")
    if not isinstance(path, str):
        return None
    base = config.source_dir.resolve()
    target = (base / path).resolve()
    rel = os.path.relpath(target, base)
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    rel_posix = Path(rel).as_posix()
    if is_ignored(rel_posix, ignore):
        return None
    return file_message(rel_posix, message.get("content", ""))


def _log_connection(state, event):
    """Record a connection change to the file log with the current live counts.

    Args:
        state (State): the shared state.
        event (str): short past-tense description (e.g. "viewer connected").
    """
    _events.info("%s | online=%d readers=%d", event, len(state.viewers), state.terminals)


async def _broadcast(state, message):
    """Send a JSON message to every connected viewer, dropping dead sockets.

    A send failure means the socket is gone but its disconnect has not yet been
    processed; we drop it here and log the drop (rather than swallowing it) so a
    persistent fan-out problem is visible in the log.

    Args:
        state (State): the shared state.
        message (dict): a wire message (file or tree).
    """
    dead = []
    for ws in list(state.viewers):
        try:
            await ws.send_json(message)
        except Exception as exc:
            _events.warning("dropping unreachable viewer socket: %r", exc)
            dead.append(ws)
    for ws in dead:
        state.viewers.discard(ws)


async def advance_view_mode(state):
    """Cycle the shared view mode one step and notify every viewer and host.

    Algorithm:
        1. Advance state.view_mode to the next entry in VIEW_MODES, wrapping
           "terminal" back to "free".
        2. Fan the new {"type":"view_mode","mode":...} message out to all viewers
           (so they apply or release the forced layout) via _broadcast.
        3. Send the same message to every host socket so an extension status bar
           tracks the live mode regardless of which trigger fired; drop any host
           socket that fails (gone but not yet reaped) and log the drop.
        4. Log the new mode to the host console (the terminal the host watches).

    Args:
        state (State): the shared broadcast state (mutated: view_mode advances).
    """
    nxt = VIEW_MODES[(VIEW_MODES.index(state.view_mode) + 1) % len(VIEW_MODES)]
    state.view_mode = nxt
    message = {"type": "view_mode", "mode": nxt}
    await _broadcast(state, message)
    dead = []
    for ws in list(state.hosts):
        try:
            await ws.send_json(message)
        except Exception as exc:
            _events.warning("dropping unreachable host socket: %r", exc)
            dead.append(ws)
    for ws in dead:
        state.hosts.discard(ws)
    _console.info("view mode -> %s", nxt)


async def _heartbeat(state, interval):
    """Emit a periodic 'viewers online' line to the host's console forever.

    Algorithm:
        Every `interval` seconds, read the live viewer count, peak, and uptime
        and log one compact line via bitforge.console. To keep an idle console
        truly quiet, a tick is suppressed when nobody is connected AND the
        counts are unchanged from the last emitted tick; the first idle tick
        after everyone leaves still prints (so the drop to zero is visible).
        Runs until cancelled at app shutdown.

    Args:
        state (State): the shared state to sample.
        interval (int): seconds between ticks.
    """
    last = None
    while True:
        await asyncio.sleep(interval)
        online = len(state.viewers)
        snapshot = (online, state.terminals)
        if online == 0 and snapshot == last:
            continue
        last = snapshot
        uptime = int((time.monotonic() - state.started_at) // 60)
        _console.info(
            "[%s]  online=%d  peak=%d  uptime=%dm",
            time.strftime("%H:%M:%S"), online, state.peak_online, uptime,
        )


def create_app(config=None):
    """Build the FastAPI app with broadcast routes.

    Args:
        config (Settings | None): configuration; if None, load from env / .env
            via load_settings().

    Returns:
        FastAPI: the configured application.
    """
    if config is None:
        config = load_settings()

    @asynccontextmanager
    async def lifespan(app):
        """Run the console heartbeat for the app's lifetime, then cancel it.

        Starts the periodic 'viewers online' reporter on startup and cancels it
        on shutdown so the task never outlives the server.
        """
        task = asyncio.create_task(_heartbeat(app.state.live, config.heartbeat_seconds))
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.state.config = config
    app.state.live = State()

    @app.websocket("/ws/host")
    async def host(ws: WebSocket, token: str = ""):
        """Authenticate host and broadcast state updates to all viewers.

        Validates token (rejects on empty config.token or mismatch with 1008),
        then loops receiving JSON. 'tree' messages update the tree and fan out
        as-is. 'file' messages are validated and normalized via
        _sanitize_file_message (sandbox + ignore + server-derived language);
        rejected files are dropped silently (not stored, not broadcast). A
        'control' message {"action":"cycle_view_mode"} cycles the shared view
        mode (see advance_view_mode). The socket is tracked in state.hosts (and
        sent the current view mode on connect) so it receives view-mode echoes.
        Removes dead sockets and exits cleanly on disconnect.

        The ignore enforcement here uses the startup config.ignore (no per-
        message disk read): a host streams a 'file' on every keystroke, so
        re-reading .env per message would be wasteful, and a blocking read in
        this handler is fragile under concurrent sockets. /file and the broadcast
        tree still hot-reload the ignore list per request; only this host-push
        defense-in-depth uses the configured list.

        Args:
            ws (WebSocket): the host connection.
            token (str): query parameter token to validate against config.token.
        """
        if not config.token or token != config.token:
            _events.warning("host auth rejected (bad or empty token)")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        state = app.state.live
        state.hosts.add(ws)
        # Initialize this host's view of the shared mode so a freshly-connected
        # extension status bar is correct without waiting for the next change.
        await ws.send_json({"type": "view_mode", "mode": state.view_mode})
        _log_connection(state, "broadcast source connected")
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "tree":
                    state.current_tree = message.get("tree", [])
                    state.current_root = message.get("root", "")
                    await _broadcast(state, message)
                elif message.get("type") == "file":
                    sanitized = _sanitize_file_message(config, message, config.ignore)
                    if sanitized is None:
                        continue  # drop unsafe/ignored paths; never store or broadcast
                    state.current_file = sanitized
                    await _broadcast(state, sanitized)
                elif message.get("type") == "control" and message.get("action") == "cycle_view_mode":
                    await advance_view_mode(state)
        except WebSocketDisconnect:
            pass
        finally:
            state.hosts.discard(ws)
            _log_connection(state, "broadcast source disconnected")

    @app.websocket("/ws/viewer")
    async def viewer(ws: WebSocket):
        """Accept viewer, send current state, then drain receive-only until disconnect.

        Accepts, registers the socket, sends current tree, current file, then the
        current view mode (late-joiner state), then drains receive-only until
        disconnect, discarding the socket in finally.

        Args:
            ws (WebSocket): the viewer connection.
        """
        await ws.accept()
        state = app.state.live
        state.viewers.add(ws)
        state.peak_online = max(state.peak_online, len(state.viewers))
        _log_connection(state, "viewer connected")
        await ws.send_json({"type": "tree", "tree": state.current_tree, "root": state.current_root})
        if state.current_file is not None:
            await ws.send_json(state.current_file)
        # Late-joiner view mode: a viewer joining mid-lock lands in the right layout.
        await ws.send_json({"type": "view_mode", "mode": state.view_mode})
        try:
            while True:
                await ws.receive_text()  # viewers send nothing; this detects disconnect
        except WebSocketDisconnect:
            pass
        finally:
            state.viewers.discard(ws)
            _log_connection(state, "viewer disconnected")

    @app.get("/file", response_class=PlainTextResponse)
    async def file(path: str):
        """Serve a single source file as plain text, sandboxed and ignore-aware.

        Algorithm:
            1. Reload settings from env / .env so hot-edits to the ignore list
               take effect immediately (enforcement never depends on a message).
            2. Resolve source_dir/path; reject (404) if it escapes source_dir.
            3. Reject (404) if missing, a directory, or ignored.
            4. Return the file text.

        Args:
            path (str): POSIX-relative path under source_dir.

        Returns:
            PlainTextResponse: file content, or 404 on any rejection.
        """
        ignore = load_settings().ignore
        base = config.source_dir.resolve()
        target = (base / path).resolve()
        rel = os.path.relpath(target, base)
        if rel.startswith("..") or os.path.isabs(rel):
            return PlainTextResponse("not found", status_code=404)
        if not target.is_file() or is_ignored(Path(rel).as_posix(), ignore):
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(target.read_text())

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the viewer page HTML (static/index.html)."""
        return HTMLResponse((_STATIC / "index.html").read_text())

    @app.websocket("/terminal/ws")
    async def terminal_ws(ws: WebSocket):
        """Reverse-proxy the viewer terminal WebSocket to ttyd (read-only).

        Pumps frames both directions between the viewer and ttyd, preserving
        ttyd's 'tty' subprotocol. ttyd itself enforces read-only (no -W,
        tmux attach -r), so nothing viewers send can affect the host.
        """
        await ws.accept(subprotocol="tty")
        state = app.state.live
        state.terminals += 1
        _log_connection(state, "terminal reader connected")
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

                tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
                _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                # Retrieve every task's outcome so a peer disconnect never surfaces
                # as an unhandled "Task exception was never retrieved". A viewer
                # going away raises WebSocketDisconnect from to_upstream; ttyd going
                # away ends to_client. Both are normal teardown for this read-only
                # proxy — swallow them (gather consumes the cancelled pending task
                # too) and let the handler return so FastAPI closes the socket.
                await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, websockets.exceptions.WebSocketException):
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        finally:
            state.terminals -= 1
            _log_connection(state, "terminal reader disconnected")

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
