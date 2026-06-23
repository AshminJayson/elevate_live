"""File-watcher that pushes code + tree updates to the broadcast server.

Pure builders (make_file_message / make_tree_message) are unit-tested. The
run() entrypoint wires watchdog and the teacher WebSocket and is verified
manually end-to-end (Task 9).
"""

import asyncio
import os
from pathlib import Path

import websockets
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from liveclass.config import Config, is_ignored, load_config
from liveclass.protocol import file_message, tree_message
from liveclass.tree import build_tree


def make_tree_message(lesson_dir, ignore):
    """Build a tree wire message for the current lesson dir.

    Args:
        lesson_dir (str | Path): root directory.
        ignore (list[str]): ignore patterns.

    Returns:
        dict: tree wire message.
    """
    return tree_message(build_tree(lesson_dir, ignore))


def make_file_message(lesson_dir, rel_path, ignore):
    """Build a file wire message for one file, or None if it should be skipped.

    Algorithm:
        Skip if the relative path is ignored, not a file, or not UTF-8
        decodable (binary). Otherwise read it and build a file message.

    Args:
        lesson_dir (str | Path): root directory.
        rel_path (str): POSIX-relative path of the changed file.
        ignore (list[str]): ignore patterns.

    Returns:
        dict | None: file wire message, or None when skipped.
    """
    if is_ignored(rel_path, ignore):
        return None
    target = Path(lesson_dir) / rel_path
    if not target.is_file():
        return None
    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return file_message(rel_path, content)


class _Handler(FileSystemEventHandler):
    """Queues (kind, rel_path) change events for the async sender.

    Attributes:
        _root: resolved lesson directory Path.
        _queue: asyncio.Queue for (kind, rel_path) tuples.
        _loop: running event loop for thread-safe queue operations.
    """

    def __init__(self, lesson_dir, queue, loop):
        """Initialize the watchdog event handler.

        Args:
            lesson_dir (str | Path): lesson directory to watch.
            queue (asyncio.Queue): queue for (kind, rel_path) events.
            loop: running asyncio event loop.
        """
        self._root = Path(lesson_dir)
        self._queue = queue
        self._loop = loop

    def _emit(self, kind, src_path):
        """Enqueue a watchdog event as (kind, rel_path) if within root.

        Algorithm:
            Resolve src_path to absolute, compute relative path from root,
            and enqueue (kind, rel_path) via thread-safe call_soon_threadsafe.

        Args:
            kind (str): event kind ("file", "tree").
            src_path (str): absolute file path from watchdog.
        """
        try:
            rel = Path(src_path).resolve().relative_to(self._root.resolve()).as_posix()
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
    """Watch the lesson dir + config and stream updates to /ws/teacher.

    Algorithm:
        Connect (with retry/backoff) to the teacher WebSocket. Start a watchdog
        observer on lesson_dir and liveclass.toml. On a file change, send a
        file message; on tree/config changes, re-send the full tree. Debounce
        rapid events by ~100ms.

    Args:
        config (Config): resolved configuration.
    """
    url = f"ws://127.0.0.1:8000/ws/teacher?token={config.token}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    handler = _Handler(config.lesson_dir, queue, loop)
    observer = Observer()
    observer.schedule(handler, str(config.lesson_dir), recursive=True)
    observer.start()

    while True:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(_json(make_tree_message(config.lesson_dir, config.ignore)))
                while True:
                    kind, rel = await queue.get()
                    await asyncio.sleep(0.1)  # debounce
                    while not queue.empty():
                        queue.get_nowait()
                    if kind == "file":
                        msg = make_file_message(config.lesson_dir, rel, config.ignore)
                        if msg:
                            await ws.send(_json(msg))
                    await ws.send(_json(make_tree_message(config.lesson_dir, config.ignore)))
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
    import json

    return json.dumps(message)


if __name__ == "__main__":
    cfg = load_config(os.environ.get("LIVECLASS_CONFIG", "liveclass.toml"))
    asyncio.run(run(cfg))
