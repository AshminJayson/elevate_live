"""File-watcher that pushes code + tree updates to the broadcast server.

Pure builders (make_file_message / make_tree_message) are unit-tested. The
run() entrypoint wires watchdog and the host WebSocket and is verified
manually end-to-end (Task 9).
"""

import asyncio
import json
from pathlib import Path

import websockets
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from bitforge.config import is_ignored, load_settings
from bitforge.protocol import file_message, tree_message
from bitforge.tree import build_tree


def make_tree_message(source_dir, ignore):
    """Build a tree wire message for the current source dir.

    Args:
        source_dir (str | Path): root directory.
        ignore (list[str]): ignore patterns.

    Returns:
        dict: tree wire message.
    """
    return tree_message(build_tree(source_dir, ignore), Path(source_dir).resolve().name)


def make_file_message(source_dir, rel_path, ignore):
    """Build a file wire message for one file, or None if it should be skipped.

    Algorithm:
        Skip if the relative path is ignored, not a file, or not UTF-8
        decodable (binary). Otherwise read it and build a file message.

    Args:
        source_dir (str | Path): root directory.
        rel_path (str): POSIX-relative path of the changed file.
        ignore (list[str]): ignore patterns.

    Returns:
        dict | None: file wire message, or None when skipped.
    """
    if is_ignored(rel_path, ignore):
        return None
    target = Path(source_dir) / rel_path
    if not target.is_file():
        return None
    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return file_message(rel_path, content)


def coalesce_burst(events):
    """Reduce a burst of change events to the file paths whose content to resend.

    Algorithm:
        A single save often arrives as a burst of events. In particular an
        atomic save (editor writes a temp file then renames it over the target,
        as Vim and VS Code do) emits a create + several modifies + a delete,
        leading with a 'tree' event. Walk the burst in arrival order and keep
        every 'file' event's rel_path, de-duplicated to its last occurrence, so
        a leading 'tree' event can never crowd out the real file update and the
        most recently touched file ends up last (the active file). The caller
        always resends the tree separately, so 'tree' events need no path here.

    Args:
        events (list[tuple[str, str]]): (kind, rel_path) events in arrival
            order; kind is "file" or "tree", rel_path is POSIX-relative ("" for
            tree-wide changes).

    Returns:
        list[str]: distinct non-empty rel_paths of modified files to resend,
            in last-touched order.
    """
    paths = []
    for kind, rel in events:
        if kind == "file" and rel:
            if rel in paths:
                paths.remove(rel)
            paths.append(rel)
    return paths


class _Handler(FileSystemEventHandler):
    """Queues (kind, rel_path) change events for the async sender.

    Attributes:
        _root: resolved source directory Path.
        _config_path: resolved Path to the config file (.env).
        _queue: asyncio.Queue for (kind, rel_path) tuples.
        _loop: running event loop for thread-safe queue operations.
    """

    def __init__(self, source_dir, queue, loop, config_path):
        """Initialize the watchdog event handler.

        Args:
            source_dir (str | Path): source directory to watch.
            queue (asyncio.Queue): queue for (kind, rel_path) events.
            loop: running asyncio event loop.
            config_path (Path): resolved path to the config file.
        """
        self._root = Path(source_dir).resolve()
        self._config_path = config_path
        self._queue = queue
        self._loop = loop

    def _emit(self, kind, src_path):
        """Enqueue a watchdog event as (kind, rel_path) if within root.

        Algorithm:
            Resolve src_path to absolute. If it equals the config file path,
            enqueue a tree event and return immediately (the config file may be
            outside source_dir and would otherwise be dropped by the ValueError
            guard). Otherwise compute relative path from root and enqueue
            (kind, rel_path) via thread-safe call_soon_threadsafe.

        Args:
            kind (str): event kind ("file", "tree").
            src_path (str): absolute file path from watchdog.
        """
        resolved = Path(src_path).resolve()
        if resolved == self._config_path:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ("tree", ""))
            return
        try:
            rel = resolved.relative_to(self._root).as_posix()
        except ValueError:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (kind, rel))

    def on_modified(self, event):
        """Queue file modifications (not directories).

        Args:
            event: watchdog FileModifiedEvent.
        """
        if not event.is_directory:
            self._emit("file", event.src_path)

    def on_created(self, event):
        """Queue creations as tree updates.

        Args:
            event: watchdog FileCreatedEvent or DirCreatedEvent.
        """
        self._emit("tree", event.src_path)

    def on_deleted(self, event):
        """Queue deletions as tree updates.

        Args:
            event: watchdog FileDeletedEvent or DirDeletedEvent.
        """
        self._emit("tree", event.src_path)

    def on_moved(self, event):
        """Queue moves/renames as tree updates.

        Args:
            event: watchdog FileMovedEvent or DirMovedEvent.
        """
        self._emit("tree", event.src_path)


async def run(config):
    """Watch the source dir + config and stream updates to /ws/host.

    Algorithm:
        Connect (with retry/backoff) to the host WebSocket. Start a watchdog
        observer on source_dir and the .env config file. On a file change, send a
        file message; on tree/config changes, re-send the full tree. Debounce
        rapid events by ~100ms.

    Args:
        config (Settings): resolved configuration.
    """
    url = f"ws://127.0.0.1:8000/ws/host?token={config.token}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    config_path = Path(".env").resolve()
    handler = _Handler(config.source_dir, queue, loop, config_path)
    observer = Observer()
    observer.schedule(handler, str(config.source_dir), recursive=True)
    if config_path.parent != Path(config.source_dir).resolve():
        observer.schedule(handler, str(config_path.parent), recursive=False)
    observer.start()

    while True:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(_json(make_tree_message(config.source_dir, config.ignore)))

                async def _pump():
                    """Drain debounced change events, pushing file/tree messages forever.

                    On each burst: wait one debounce window, drain every queued
                    event, then resend the content of each distinct modified file
                    (via coalesce_burst, so an atomic-save burst that leads with a
                    create/'tree' event never drops the file update) and finally
                    resend the tree."""
                    while True:
                        first = await queue.get()
                        await asyncio.sleep(0.1)  # debounce: let the rest of the burst arrive
                        burst = [first]
                        while not queue.empty():
                            burst.append(queue.get_nowait())
                        for rel_path in coalesce_burst(burst):
                            msg = make_file_message(config.source_dir, rel_path, config.ignore)
                            if msg is not None:
                                await ws.send(_json(msg))
                        await ws.send(_json(make_tree_message(config.source_dir, config.ignore)))

                async def _watch_closed():
                    """Return (or raise) when the host socket closes; the host
                    receives nothing, so iterating the socket simply blocks until the
                    connection drops. This makes an idle disconnect trigger a reconnect
                    instead of leaving the pump blocked on an empty queue forever."""
                    async for _ in ws:
                        pass

                pump_task = asyncio.ensure_future(_pump())
                watch_task = asyncio.ensure_future(_watch_closed())
                done, pending = await asyncio.wait(
                    {pump_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                for task in done:
                    task.result()  # re-raise a pump send error if that is what completed
                raise ConnectionError("host socket closed")  # watch completed -> reconnect
        except Exception as exc:  # surface loudly, then retry
            print(f"[broadcaster] connection lost: {exc!r}; retrying in 2s")
            await asyncio.sleep(2)


def _json(message):
    """Serialize a wire message to a JSON string.

    Args:
        message (dict): wire message dict.

    Returns:
        str: JSON string.
    """
    return json.dumps(message)


if __name__ == "__main__":
    asyncio.run(run(load_settings()))
