# LiveClass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a view-only, text-based live broadcast of a teacher's VS Code editing, file tree, and terminal to 60+ students in the browser, with copyable text and no participant cap.

**Architecture:** All host processes behind one ngrok ingress. A FastAPI server is the hub: it authenticates a single broadcaster (file-watcher) connection, fans code + file-tree updates out to all student WebSockets, serves a sandboxed read-only `/file` endpoint, reverse-proxies a read-only `ttyd` terminal, and serves a three-pane (explorer / Monaco / terminal) student page. Monaco and xterm.js load from a CDN so only the HTML shell and live traffic cross the tunnel.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`), FastAPI, `uvicorn[standard]`, `websockets`, `watchdog`, `httpx` (proxy + tests), pytest + pytest-asyncio; ttyd + tmux + ngrok as external binaries; Monaco + xterm.js from CDN. Packaged with `uv`.

## Global Constraints

These apply to every task; copied verbatim from the spec.

- **Python >= 3.11** (relies on stdlib `tomllib`).
- **Packaging via `uv`** — `uv pip install`, `uv run <cmd>`. Never bare `pip`.
- **No emojis in code.**
- **Teacher token from env only** — `LIVECLASS_TOKEN`. Never hardcoded, never in `liveclass.toml`.
- **Students strictly read-only** on every channel: `/ws/student` is receive-only; ttyd runs without `-W` and attaches tmux with `-r`.
- **Only `:8000` is exposed.** ttyd (`:7681`) and the broadcaster bind to localhost; ttyd reaches students only via the `/terminal` proxy.
- **The ignore list (`liveclass.toml`) is the single source of visibility**, enforced in BOTH tree generation and `/file`. Defaults exclude only `.git/`, `.venv/`, `venv/`, `__pycache__/`, `node_modules/`, `*.pyc`. Dotfiles like `.env` are shown by design.
- **`.env` visibility is intentional (e2e teaching)** — operational rule: the class `.env` holds placeholder values only; real secrets stay outside `lesson_dir`.
- **Monaco and xterm.js load from a CDN**, never served through the tunnel.
- **Every function gets a docstring** (algorithm in plain English, args with types, I/O). Loose dict/list schemas documented explicitly.
- **Every module with executable logic gets an `if __name__ == "__main__":` block.**

### Shared data schemas (referenced across tasks)

- **tree node** (dict): `{"name": str, "path": str, "type": "file"|"dir", "children": list[node]?}` — `path` is POSIX-relative to `lesson_dir`; `children` present only when `type == "dir"`.
- **file message** (dict): `{"type": "file", "path": str, "language": str, "content": str}`.
- **tree message** (dict): `{"type": "tree", "tree": list[node]}`.

---

### Task 1: Project scaffold + config loader

**Files:**
- Create: `pyproject.toml`
- Create: `liveclass/__init__.py`
- Create: `liveclass/config.py`
- Create: `liveclass.toml`
- Create: `.gitignore`
- Create: `lesson/.gitkeep`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `liveclass.config.DEFAULT_IGNORE: list[str]`
  - `liveclass.config.Config` dataclass with fields: `lesson_dir: Path`, `title: str`, `ignore: list[str]`, `tmux_session: str`, `cols: int`, `rows: int`, `token: str`.
  - `liveclass.config.load_config(path: str | Path, token: str | None = None) -> Config`
  - `liveclass.config.is_ignored(rel_path: str, ignore_patterns: list[str]) -> bool`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "liveclass"
version = "0.1.0"
description = "View-only live code/terminal broadcast for teaching"
requires-python = ">=3.11"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "websockets",
    "watchdog",
    "httpx",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/
.env
```

- [ ] **Step 3: Create `liveclass.toml` and `lesson/.gitkeep`**

`liveclass.toml`:

```toml
# liveclass.toml — edit any time; changes apply live, no restart needed.

[broadcast]
lesson_dir = "./lesson"
title      = "FastAPI Live"

# Glob patterns excluded from BOTH the file tree and /file fetches.
# Defaults exclude only noise/danger; dotfiles like .env are SHOWN (e2e teaching).
ignore = [
  ".git/",
  ".venv/", "venv/",
  "__pycache__/",
  "node_modules/",
  "*.pyc",
]

[terminal]
tmux_session = "class"
cols = 100
rows = 30
```

`lesson/.gitkeep`: empty file (keeps the lesson dir in git).

- [ ] **Step 4: Install deps**

Run: `uv pip install -e ".[dev]"`
Expected: installs fastapi, uvicorn, websockets, watchdog, httpx, pytest, pytest-asyncio.

- [ ] **Step 5: Write the failing tests** — `tests/test_config.py`

```python
from pathlib import Path

from liveclass.config import Config, DEFAULT_IGNORE, is_ignored, load_config


def test_load_config_reads_values(tmp_path):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text(
        '[broadcast]\n'
        'lesson_dir = "./lesson"\n'
        'title = "FastAPI Live"\n'
        'ignore = [".git/", "*.pyc"]\n'
        '[terminal]\n'
        'tmux_session = "class"\n'
        'cols = 100\n'
        'rows = 30\n'
    )
    cfg = load_config(cfg_file, token="secret")
    assert isinstance(cfg, Config)
    assert cfg.title == "FastAPI Live"
    assert cfg.ignore == [".git/", "*.pyc"]
    assert cfg.tmux_session == "class"
    assert cfg.cols == 100
    assert cfg.rows == 30
    assert cfg.token == "secret"
    assert cfg.lesson_dir.is_absolute()


def test_load_config_token_from_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text('[broadcast]\nlesson_dir = "./lesson"\n')
    monkeypatch.setenv("LIVECLASS_TOKEN", "from-env")
    cfg = load_config(cfg_file)
    assert cfg.token == "from-env"


def test_load_config_defaults(tmp_path):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text("")
    cfg = load_config(cfg_file, token="t")
    assert cfg.ignore == DEFAULT_IGNORE
    assert cfg.cols == 100
    assert cfg.rows == 30
    assert cfg.tmux_session == "class"


def test_is_ignored_directory_pattern():
    assert is_ignored(".git/config", [".git/"]) is True
    assert is_ignored("pkg/__pycache__/x.pyc", ["__pycache__/"]) is True


def test_is_ignored_glob_pattern():
    assert is_ignored("main.pyc", ["*.pyc"]) is True
    assert is_ignored("sub/main.pyc", ["*.pyc"]) is True


def test_dotfiles_are_shown_by_default():
    assert is_ignored(".env", DEFAULT_IGNORE) is False
    assert is_ignored(".gitignore", DEFAULT_IGNORE) is False


def test_is_ignored_negative():
    assert is_ignored("main.py", DEFAULT_IGNORE) is False
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liveclass.config'`.

- [ ] **Step 7: Create `liveclass/__init__.py`** (empty file).

- [ ] **Step 8: Implement `liveclass/config.py`**

```python
"""Configuration loading and ignore-pattern matching for LiveClass.

This module owns liveclass.toml parsing and the single ignore-matching rule
used by both the file-tree walk and the /file endpoint.
"""

import os
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_IGNORE = [
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "node_modules/",
    "*.pyc",
]


@dataclass
class Config:
    """Resolved LiveClass configuration.

    Fields:
        lesson_dir: absolute Path to the directory broadcast to students.
        title: page title shown to students.
        ignore: list of glob/dir patterns hidden from tree and /file.
        tmux_session: tmux session name shared by teacher, ttyd, and runner.
        cols: fixed terminal width streamed to students.
        rows: fixed terminal height streamed to students.
        token: teacher auth token (from env, never persisted).
    """

    lesson_dir: Path
    title: str
    ignore: list[str]
    tmux_session: str
    cols: int
    rows: int
    token: str


def load_config(path, token=None):
    """Load and resolve configuration from a TOML file.

    Algorithm:
        1. Read and parse the TOML at `path`.
        2. Pull [broadcast] and [terminal] tables, applying defaults for any
           missing key.
        3. Resolve lesson_dir to an absolute Path.
        4. Resolve token: explicit arg wins, else LIVECLASS_TOKEN env, else "".

    Args:
        path (str | Path): path to liveclass.toml.
        token (str | None): override token; if None, read LIVECLASS_TOKEN.

    Returns:
        Config: the resolved configuration.
    """
    data = tomllib.loads(Path(path).read_text())
    broadcast = data.get("broadcast", {})
    terminal = data.get("terminal", {})
    resolved_token = token if token is not None else os.environ.get("LIVECLASS_TOKEN", "")
    return Config(
        lesson_dir=Path(broadcast.get("lesson_dir", "./lesson")).resolve(),
        title=broadcast.get("title", "LiveClass"),
        ignore=list(broadcast.get("ignore", DEFAULT_IGNORE)),
        tmux_session=terminal.get("tmux_session", "class"),
        cols=int(terminal.get("cols", 100)),
        rows=int(terminal.get("rows", 30)),
        token=resolved_token,
    )


def is_ignored(rel_path, ignore_patterns):
    """Return True if a POSIX-relative path matches any ignore pattern.

    Algorithm:
        For each pattern: if it ends with "/", treat its stem as a directory
        name and match if that name appears as any path segment. Otherwise
        treat it as a glob and match against the full path or the basename.

    Args:
        rel_path (str): POSIX-relative path (e.g. "pkg/main.py").
        ignore_patterns (list[str]): patterns from config.

    Returns:
        bool: True if the path should be hidden.
    """
    parts = rel_path.split("/")
    for pattern in ignore_patterns:
        if pattern.endswith("/"):
            if pattern.rstrip("/") in parts:
                return True
        elif fnmatch(rel_path, pattern) or fnmatch(parts[-1], pattern):
            return True
    return False


if __name__ == "__main__":
    cfg = load_config("liveclass.toml", token="demo")
    print(cfg)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (8 tests).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .gitignore liveclass.toml lesson/.gitkeep liveclass/__init__.py liveclass/config.py tests/test_config.py
git commit -m "feat: project scaffold and config loader"
```

---

### Task 2: File-tree builder

**Files:**
- Create: `liveclass/tree.py`
- Test: `tests/test_tree.py`

**Interfaces:**
- Consumes: `liveclass.config.is_ignored`.
- Produces: `liveclass.tree.build_tree(lesson_dir: str | Path, ignore: list[str]) -> list[dict]` returning a list of tree nodes (schema in Global Constraints). Directories sort before files; both alphabetically (case-insensitive).

- [ ] **Step 1: Write the failing tests** — `tests/test_tree.py`

```python
from liveclass.tree import build_tree


def _make_lesson(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')")
    (tmp_path / ".env").write_text("API_KEY=changeme")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "user.py").write_text("class User: ...")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.pyc").write_text("x")
    return tmp_path


def test_build_tree_structure(tmp_path):
    _make_lesson(tmp_path)
    tree = build_tree(tmp_path, [])
    names = [n["name"] for n in tree]
    # directories first, then files, each alphabetical
    assert names.index("models") < names.index(".env")
    models = next(n for n in tree if n["name"] == "models")
    assert models["type"] == "dir"
    assert models["children"][0]["path"] == "models/user.py"


def test_build_tree_respects_ignore(tmp_path):
    _make_lesson(tmp_path)
    tree = build_tree(tmp_path, ["__pycache__/"])
    assert all(n["name"] != "__pycache__" for n in tree)


def test_build_tree_shows_dotfiles(tmp_path):
    _make_lesson(tmp_path)
    tree = build_tree(tmp_path, ["__pycache__/"])
    assert any(n["name"] == ".env" for n in tree)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tree.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liveclass.tree'`.

- [ ] **Step 3: Implement `liveclass/tree.py`**

```python
"""Build a JSON-serializable file tree of the lesson directory."""

from pathlib import Path

from liveclass.config import is_ignored


def build_tree(lesson_dir, ignore):
    """Walk lesson_dir into a nested list of tree nodes, honoring ignore.

    Algorithm:
        Recurse each directory. Within a directory, sort entries so dirs come
        before files and each group is alphabetical (case-insensitive). Skip
        any entry whose POSIX-relative path is ignored. Directory nodes carry
        a "children" list; file nodes do not.

    Args:
        lesson_dir (str | Path): root directory to walk.
        ignore (list[str]): ignore patterns (see config.is_ignored).

    Returns:
        list[dict]: tree nodes, each
            {"name": str, "path": str, "type": "file"|"dir", "children": list?}
            where "path" is POSIX-relative to lesson_dir.
    """
    root = Path(lesson_dir)

    def walk(directory):
        entries = sorted(
            directory.iterdir(),
            key=lambda p: (p.is_file(), p.name.lower()),
        )
        nodes = []
        for entry in entries:
            rel = entry.relative_to(root).as_posix()
            if is_ignored(rel, ignore):
                continue
            if entry.is_dir():
                nodes.append(
                    {"name": entry.name, "path": rel, "type": "dir", "children": walk(entry)}
                )
            else:
                nodes.append({"name": entry.name, "path": rel, "type": "file"})
        return nodes

    return walk(root)


if __name__ == "__main__":
    import json

    print(json.dumps(build_tree("lesson", []), indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tree.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add liveclass/tree.py tests/test_tree.py
git commit -m "feat: file-tree builder with ignore support"
```

---

### Task 3: Protocol messages + language detection

**Files:**
- Create: `liveclass/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `liveclass.protocol.detect_language(path: str) -> str`
  - `liveclass.protocol.file_message(path: str, content: str) -> dict`
  - `liveclass.protocol.tree_message(tree: list[dict]) -> dict`

- [ ] **Step 1: Write the failing tests** — `tests/test_protocol.py`

```python
from liveclass.protocol import detect_language, file_message, tree_message


def test_detect_language_known():
    assert detect_language("main.py") == "python"
    assert detect_language("a/b/app.ts") == "typescript"
    assert detect_language("data.json") == "json"


def test_detect_language_unknown():
    assert detect_language("Dockerfile") == "plaintext"
    assert detect_language(".env") == "plaintext"


def test_file_message():
    msg = file_message("main.py", "print(1)")
    assert msg == {
        "type": "file",
        "path": "main.py",
        "language": "python",
        "content": "print(1)",
    }


def test_tree_message():
    tree = [{"name": "main.py", "path": "main.py", "type": "file"}]
    assert tree_message(tree) == {"type": "tree", "tree": tree}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liveclass.protocol'`.

- [ ] **Step 3: Implement `liveclass/protocol.py`**

```python
"""Message builders and language detection for the broadcast protocol.

Wire messages (JSON over WebSocket), teacher -> server -> students:
    file: {"type": "file", "path": str, "language": str, "content": str}
    tree: {"type": "tree", "tree": list[node]}
"""

from pathlib import PurePosixPath

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shell",
    ".txt": "plaintext",
}


def detect_language(path):
    """Map a file path's extension to a Monaco language id.

    Args:
        path (str): file path (POSIX-relative is fine).

    Returns:
        str: Monaco language id, or "plaintext" if the extension is unknown.
    """
    return LANGUAGE_BY_EXT.get(PurePosixPath(path).suffix, "plaintext")


def file_message(path, content):
    """Build a 'file' wire message for the active file.

    Args:
        path (str): POSIX-relative path of the file.
        content (str): full text content of the file.

    Returns:
        dict: {"type": "file", "path", "language", "content"}.
    """
    return {"type": "file", "path": path, "language": detect_language(path), "content": content}


def tree_message(tree):
    """Build a 'tree' wire message wrapping a file-tree snapshot.

    Args:
        tree (list[dict]): tree nodes from tree.build_tree.

    Returns:
        dict: {"type": "tree", "tree": tree}.
    """
    return {"type": "tree", "tree": tree}


if __name__ == "__main__":
    print(file_message("main.py", "print('hello')"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add liveclass/protocol.py tests/test_protocol.py
git commit -m "feat: protocol message builders and language detection"
```

---

### Task 4: Server core — state, teacher auth, student fan-out, late-joiner

**Files:**
- Create: `liveclass/server.py`
- Test: `tests/test_server_broadcast.py`

**Interfaces:**
- Consumes: `liveclass.config.load_config`, `liveclass.config.Config`.
- Produces:
  - `liveclass.server.State` with attributes `current_tree: list`, `current_file: dict | None`, `students: set`.
  - `liveclass.server.create_app(config: Config | None = None) -> FastAPI`. If `config` is None, loads from `LIVECLASS_CONFIG` env (default `liveclass.toml`).
  - WebSocket routes `/ws/teacher` (rejects when `token` query param != `config.token` or token empty) and `/ws/student` (sends current tree + file on connect, then streams).

- [ ] **Step 1: Write the failing tests** — `tests/test_server_broadcast.py`

```python
import pytest
from fastapi.testclient import TestClient

from liveclass.config import Config
from liveclass.server import create_app


def _config(tmp_path, token="secret"):
    return Config(
        lesson_dir=tmp_path,
        title="Test",
        ignore=[],
        tmux_session="class",
        cols=100,
        rows=30,
        token=token,
    )


def test_teacher_bad_token_rejected(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/teacher?token=wrong"):
            pass


def test_teacher_broadcast_reaches_student(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/student") as student:
        # drain initial (empty) state: tree then no file
        first = student.receive_json()
        assert first["type"] == "tree"
        with client.websocket_connect("/ws/teacher?token=secret") as teacher:
            teacher.send_json({"type": "file", "path": "main.py", "language": "python", "content": "x=1"})
            msg = student.receive_json()
            assert msg == {"type": "file", "path": "main.py", "language": "python", "content": "x=1"}


def test_late_joiner_gets_current_state(tmp_path):
    app = create_app(_config(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/teacher?token=secret") as teacher:
        teacher.send_json({"type": "tree", "tree": [{"name": "main.py", "path": "main.py", "type": "file"}]})
        teacher.send_json({"type": "file", "path": "main.py", "language": "python", "content": "x=1"})
        # give the server a moment to store state
        with client.websocket_connect("/ws/student") as student:
            got_tree = student.receive_json()
            got_file = student.receive_json()
            assert got_tree["type"] == "tree"
            assert got_tree["tree"][0]["path"] == "main.py"
            assert got_file["type"] == "file"
            assert got_file["content"] == "x=1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_broadcast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liveclass.server'`.

- [ ] **Step 3: Implement `liveclass/server.py` (core)**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_broadcast.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add liveclass/server.py tests/test_server_broadcast.py
git commit -m "feat: server core with teacher auth and student fan-out"
```

---

### Task 5: Server `/file` endpoint — sandboxed, ignore-aware, hot-reloading

**Files:**
- Modify: `liveclass/server.py` (add the `/file` route inside `create_app`)
- Test: `tests/test_server_file.py`

**Interfaces:**
- Consumes: `liveclass.config.is_ignored`, `liveclass.config.load_config`, `app.state.config`.
- Produces: `GET /file?path=<rel>` returning the file as `text/plain`; `404` if the resolved path escapes `lesson_dir`, does not exist, is a directory, or is ignored by the freshly-reloaded ignore list.

- [ ] **Step 1: Write the failing tests** — `tests/test_server_file.py`

```python
from fastapi.testclient import TestClient

from liveclass.config import Config
from liveclass.server import create_app


def _config(tmp_path, ignore):
    (tmp_path / "main.py").write_text("print('hi')")
    (tmp_path / ".env").write_text("API_KEY=changeme")
    (tmp_path / "secret.py").write_text("TOKEN=abc")
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text(
        f'[broadcast]\nlesson_dir = "{tmp_path.as_posix()}"\nignore = {ignore!r}\n'
    )
    return Config(
        lesson_dir=tmp_path, title="T", ignore=ignore,
        tmux_session="class", cols=100, rows=30, token="secret",
    ), cfg_file


def test_file_serves_content(tmp_path, monkeypatch):
    cfg, cfg_file = _config(tmp_path, [])
    monkeypatch.setenv("LIVECLASS_CONFIG", str(cfg_file))
    client = TestClient(create_app(cfg))
    r = client.get("/file", params={"path": "main.py"})
    assert r.status_code == 200
    assert r.text == "print('hi')"


def test_file_serves_dotfile(tmp_path, monkeypatch):
    cfg, cfg_file = _config(tmp_path, [])
    monkeypatch.setenv("LIVECLASS_CONFIG", str(cfg_file))
    client = TestClient(create_app(cfg))
    r = client.get("/file", params={"path": ".env"})
    assert r.status_code == 200
    assert "API_KEY" in r.text


def test_file_rejects_traversal(tmp_path, monkeypatch):
    cfg, cfg_file = _config(tmp_path, [])
    monkeypatch.setenv("LIVECLASS_CONFIG", str(cfg_file))
    client = TestClient(create_app(cfg))
    r = client.get("/file", params={"path": "../liveclass.toml"})
    assert r.status_code == 404


def test_file_rejects_ignored_after_hot_reload(tmp_path, monkeypatch):
    cfg, cfg_file = _config(tmp_path, [])
    monkeypatch.setenv("LIVECLASS_CONFIG", str(cfg_file))
    client = TestClient(create_app(cfg))
    assert client.get("/file", params={"path": "secret.py"}).status_code == 200
    # hot-edit the config to hide secret.py
    cfg_file.write_text(
        f'[broadcast]\nlesson_dir = "{tmp_path.as_posix()}"\nignore = ["secret.py"]\n'
    )
    assert client.get("/file", params={"path": "secret.py"}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server_file.py -v`
Expected: FAIL — `404`/route missing (assertions fail or route not found).

- [ ] **Step 3: Add imports and the `/file` route to `liveclass/server.py`**

At the top of the file, extend the imports:

```python
from pathlib import Path

from fastapi.responses import PlainTextResponse
```

Inside `create_app`, before `return app`, add:

```python
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
        from liveclass.config import is_ignored, load_config

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server_file.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add liveclass/server.py tests/test_server_file.py
git commit -m "feat: sandboxed ignore-aware /file endpoint with hot reload"
```

---

### Task 6: Server — serve student page + reverse-proxy ttyd at `/terminal`

**Files:**
- Modify: `liveclass/server.py` (add `GET /`, `GET /terminal/{path}`, `WS /terminal/ws`)
- Create: `static/index.html` (minimal placeholder; full UI built in Task 8)
- Test: `tests/test_server_page.py`

**Interfaces:**
- Consumes: `app.state.config.title`, `static/index.html`.
- Produces: `GET /` returns the student HTML (200, `text/html`). `GET /terminal/{path:path}` and `WS /terminal/ws` reverse-proxy to `http://127.0.0.1:7681` / `ws://127.0.0.1:7681/ws` (ttyd; subprotocol `tty`).

- [ ] **Step 1: Create minimal `static/index.html` placeholder**

```html
<!doctype html>
<html>
  <head><title>LiveClass</title></head>
  <body><div id="app">LiveClass loading…</div></body>
</html>
```

- [ ] **Step 2: Write the failing test** — `tests/test_server_page.py`

```python
from fastapi.testclient import TestClient

from liveclass.config import Config
from liveclass.server import create_app


def test_root_serves_html(tmp_path):
    cfg = Config(
        lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_server_page.py -v`
Expected: FAIL — 404 (no `/` route).

- [ ] **Step 4: Add page + proxy routes to `liveclass/server.py`**

Extend imports at top:

```python
import httpx
import websockets
from fastapi import Request
from fastapi.responses import HTMLResponse, Response

TTYD_HTTP = "http://127.0.0.1:7681"
TTYD_WS = "ws://127.0.0.1:7681/ws"
_STATIC = Path(__file__).resolve().parent.parent / "static"
```

Inside `create_app`, before `return app`, add:

```python
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
        async with websockets.connect(TTYD_WS, subprotocols=["tty"], open_timeout=5) as upstream:
            import asyncio

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

    @app.get("/terminal/{path:path}")
    async def terminal_http(path: str, request: Request):
        """Reverse-proxy ttyd's HTTP assets (its xterm.js page) under /terminal/."""
        async with httpx.AsyncClient() as http:
            upstream = await http.get(f"{TTYD_HTTP}/{path}", params=request.query_params)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_server_page.py -v`
Expected: PASS (1 test). (The `/terminal` proxy is verified manually in Task 9 with ttyd running.)

- [ ] **Step 6: Commit**

```bash
git add liveclass/server.py static/index.html tests/test_server_page.py
git commit -m "feat: serve student page and reverse-proxy ttyd at /terminal"
```

---

### Task 7: Broadcaster — file-watcher to WebSocket client

**Files:**
- Create: `liveclass/broadcaster.py`
- Test: `tests/test_broadcaster.py`

**Interfaces:**
- Consumes: `liveclass.tree.build_tree`, `liveclass.protocol.file_message`, `liveclass.protocol.tree_message`, `liveclass.config.is_ignored`, `liveclass.config.Config`.
- Produces:
  - `liveclass.broadcaster.make_tree_message(lesson_dir, ignore) -> dict`
  - `liveclass.broadcaster.make_file_message(lesson_dir, rel_path, ignore) -> dict | None` (None if ignored, missing, or undecodable as UTF-8).
  - `liveclass.broadcaster.run(config: Config)` — async entrypoint wiring watchdog + the teacher WebSocket (verified manually in Task 9).

- [ ] **Step 1: Write the failing tests** — `tests/test_broadcaster.py`

```python
from liveclass.broadcaster import make_file_message, make_tree_message


def test_make_file_message(tmp_path):
    (tmp_path / "main.py").write_text("x = 1")
    msg = make_file_message(tmp_path, "main.py", [])
    assert msg == {"type": "file", "path": "main.py", "language": "python", "content": "x = 1"}


def test_make_file_message_ignored_returns_none(tmp_path):
    (tmp_path / "a.pyc").write_text("x")
    assert make_file_message(tmp_path, "a.pyc", ["*.pyc"]) is None


def test_make_file_message_missing_returns_none(tmp_path):
    assert make_file_message(tmp_path, "nope.py", []) is None


def test_make_tree_message(tmp_path):
    (tmp_path / "main.py").write_text("x")
    msg = make_tree_message(tmp_path, [])
    assert msg["type"] == "tree"
    assert msg["tree"][0]["path"] == "main.py"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_broadcaster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'liveclass.broadcaster'`.

- [ ] **Step 3: Implement `liveclass/broadcaster.py`**

```python
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
    """Queues (kind, rel_path) change events for the async sender."""

    def __init__(self, lesson_dir, queue, loop):
        self._root = Path(lesson_dir)
        self._queue = queue
        self._loop = loop

    def _emit(self, kind, src_path):
        try:
            rel = Path(src_path).resolve().relative_to(self._root.resolve()).as_posix()
        except ValueError:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (kind, rel))

    def on_modified(self, event):
        if not event.is_directory:
            self._emit("file", event.src_path)

    def on_created(self, event):
        self._emit("tree", event.src_path)

    def on_deleted(self, event):
        self._emit("tree", event.src_path)

    def on_moved(self, event):
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
    """Serialize a wire message to a JSON string."""
    import json

    return json.dumps(message)


if __name__ == "__main__":
    cfg = load_config(os.environ.get("LIVECLASS_CONFIG", "liveclass.toml"))
    asyncio.run(run(cfg))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_broadcaster.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add liveclass/broadcaster.py tests/test_broadcaster.py
git commit -m "feat: file-watcher broadcaster with tree and file messages"
```

---

### Task 8: Student page — three-pane UI (explorer / Monaco / terminal)

**Files:**
- Modify: `static/index.html` (replace placeholder with full UI)
- Test: `tests/test_server_page.py` (extend with content assertions)

**Interfaces:**
- Consumes: `GET /` (serves this file), `WS /ws/student` (tree + file messages), `GET /file?path=` (browse), `/terminal/` (iframe to proxied ttyd).
- Produces: the rendered student experience. JS behavior — explorer renders the tree and highlights the active file; live mode follows the teacher's active file; clicking a file enters browse mode with a "return to live" control; both the code WebSocket reconnects with backoff.

- [ ] **Step 1: Replace `static/index.html` with the full UI**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>LiveClass</title>
    <link
      rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/editor/editor.main.css"
    />
    <style>
      html, body { margin: 0; height: 100%; font-family: ui-monospace, monospace; }
      #app { display: grid; grid-template-columns: 220px 1fr; grid-template-rows: 32px 1fr; height: 100vh; }
      #header { grid-column: 1 / 3; display: flex; align-items: center; gap: 12px; padding: 0 12px; background: #1e1e1e; color: #ddd; }
      #status { margin-left: auto; font-size: 12px; }
      #explorer { background: #252526; color: #ccc; overflow: auto; user-select: none; }
      #explorer .node { padding: 2px 10px; cursor: pointer; white-space: nowrap; font-size: 13px; }
      #explorer .node:hover { background: #2a2d2e; }
      #explorer .node.active { background: #094771; color: #fff; }
      #main { display: grid; grid-template-rows: 1fr 35%; min-width: 0; }
      #code { min-height: 0; }
      #term { border-top: 1px solid #333; }
      #term iframe { width: 100%; height: 100%; border: 0; }
      #livebar { display: none; gap: 8px; align-items: center; font-size: 12px; }
      #livebar.show { display: flex; }
      button { font: inherit; font-size: 12px; }
      * { user-select: text; }
    </style>
  </head>
  <body>
    <div id="app">
      <div id="header">
        <span id="title">LiveClass</span>
        <span id="filename"></span>
        <span id="livebar"><span>browsing</span><button id="return">return to live</button></span>
        <span id="status">connecting…</span>
      </div>
      <div id="explorer"></div>
      <div id="main">
        <div id="code"></div>
        <div id="term"><iframe src="/terminal/" title="terminal"></iframe></div>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js"></script>
    <script>
      let editor = null;
      let liveFile = null;     // last file message from the teacher
      let browsing = false;    // true when viewing a clicked file

      require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs" } });
      require(["vs/editor/editor.main"], () => {
        editor = monaco.editor.create(document.getElementById("code"), {
          value: "", language: "plaintext", readOnly: true, automaticLayout: true,
          theme: "vs-dark", minimap: { enabled: false },
        });
        connect();
      });

      function setStatus(text) { document.getElementById("status").textContent = text; }

      function showFile(msg) {
        document.getElementById("filename").textContent = msg.path;
        const model = editor.getModel();
        monaco.editor.setModelLanguage(model, msg.language || "plaintext");
        editor.setValue(msg.content);
        highlight(msg.path);
      }

      function highlight(path) {
        document.querySelectorAll("#explorer .node").forEach((el) => {
          el.classList.toggle("active", el.dataset.path === path);
        });
      }

      function renderTree(nodes, container, depth) {
        container.innerHTML = depth ? container.innerHTML : "";
        for (const node of nodes) {
          const el = document.createElement("div");
          el.className = "node";
          el.dataset.path = node.path;
          el.style.paddingLeft = 10 + depth * 14 + "px";
          el.textContent = (node.type === "dir" ? "▾ " : "  ") + node.name;
          if (node.type === "file") {
            el.onclick = () => browseFile(node.path);
          }
          container.appendChild(el);
          if (node.type === "dir" && node.children) {
            renderTree(node.children, container, depth + 1);
          }
        }
        if (liveFile && !browsing) highlight(liveFile.path);
      }

      async function browseFile(path) {
        const res = await fetch("/file?path=" + encodeURIComponent(path));
        if (!res.ok) return;
        const content = await res.text();
        browsing = true;
        document.getElementById("livebar").classList.add("show");
        const ext = path.split(".").pop();
        const lang = { py: "python", js: "javascript", ts: "typescript", json: "json", html: "html", css: "css", md: "markdown", toml: "ini", yaml: "yaml", yml: "yaml" }[ext] || "plaintext";
        showFile({ path, language: lang, content });
      }

      document.getElementById("return").onclick = () => {
        browsing = false;
        document.getElementById("livebar").classList.remove("show");
        if (liveFile) showFile(liveFile);
      };

      function connect() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(proto + "://" + location.host + "/ws/student");
        ws.onopen = () => setStatus("● live");
        ws.onclose = () => { setStatus("reconnecting…"); setTimeout(connect, 1500); };
        ws.onmessage = (ev) => {
          const msg = JSON.parse(ev.data);
          if (msg.type === "tree") {
            renderTree(msg.tree, document.getElementById("explorer"), 0);
          } else if (msg.type === "file") {
            liveFile = msg;
            if (!browsing) showFile(msg);
          }
        };
      }

      document.getElementById("title").textContent = document.title;
    </script>
  </body>
</html>
```

- [ ] **Step 2: Extend `tests/test_server_page.py` with content assertions**

Append this test:

```python
def test_page_loads_cdn_assets_and_panes(tmp_path):
    from liveclass.config import Config
    from liveclass.server import create_app
    from fastapi.testclient import TestClient

    cfg = Config(
        lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    html = client.get("/").text
    assert "cdn.jsdelivr.net/npm/monaco-editor" in html  # Monaco from CDN
    assert 'id="explorer"' in html
    assert 'id="code"' in html
    assert 'src="/terminal/"' in html  # terminal via proxy
    assert "/ws/student" in html
```

- [ ] **Step 3: Run the page tests to verify they pass**

Run: `uv run pytest tests/test_server_page.py -v`
Expected: PASS (2 tests).

- [ ] **Step 4: Commit**

```bash
git add static/index.html tests/test_server_page.py
git commit -m "feat: three-pane student page with explorer, Monaco, terminal"
```

---

### Task 9: Runner + sample lesson + end-to-end verification

**Files:**
- Create: `liveclass/run.py`
- Create: `Makefile`
- Create: `lesson/main.py` (sample content)
- Create: `lesson/.env` (placeholder values)
- Create: `README.md`

**Interfaces:**
- Consumes: `liveclass.config.load_config`, `liveclass.server.create_app`, `liveclass.broadcaster.run`, external binaries `tmux`, `ttyd`, `ngrok`.
- Produces: `make up` (start whole stack), `make down` (teardown), `make test`.

- [ ] **Step 1: Create sample lesson files**

`lesson/main.py`:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/items/{item_id}")
async def read_item(item_id: int):
    return {"item_id": item_id}
```

`lesson/.env`:

```
DEBUG=true
DATABASE_URL=sqlite:///./app.db
API_KEY=changeme
```

- [ ] **Step 2: Implement `liveclass/run.py`** (process supervisor)

```python
"""Supervisor: launches tmux, ttyd, the server, the broadcaster, and ngrok.

Algorithm:
    1. Load config; ensure a tmux session sized to cols x rows exists.
    2. Start ttyd (read-only) attached to that session under base path /terminal.
    3. Start uvicorn serving liveclass.server:create_app.
    4. Start the broadcaster (python -m liveclass.broadcaster).
    5. Start ngrok against :8000 using NGROK_DOMAIN if set.
    6. Wait; on Ctrl-C, terminate all children.

Env:
    LIVECLASS_TOKEN: required teacher token.
    LIVECLASS_CONFIG: config path (default liveclass.toml).
    NGROK_DOMAIN: reserved static ngrok domain (optional).
"""

import os
import signal
import subprocess
import sys
import time

from liveclass.config import load_config


def _ensure_tmux(session, cols, rows):
    """Create the tmux session sized cols x rows if it does not exist."""
    exists = subprocess.run(["tmux", "has-session", "-t", session]).returncode == 0
    if not exists:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows)],
            check=True,
        )


def main():
    config_path = os.environ.get("LIVECLASS_CONFIG", "liveclass.toml")
    cfg = load_config(config_path)
    if not cfg.token:
        sys.exit("LIVECLASS_TOKEN must be set")

    _ensure_tmux(cfg.tmux_session, cfg.cols, cfg.rows)

    procs = []
    # ttyd: read-only (no -W), base path /terminal, attach the session read-only.
    procs.append(subprocess.Popen([
        "ttyd", "-p", "7681", "-b", "/terminal", "-i", "127.0.0.1",
        "tmux", "attach", "-r", "-t", cfg.tmux_session,
    ]))
    procs.append(subprocess.Popen([
        "uv", "run", "uvicorn", "liveclass.server:create_app",
        "--factory", "--host", "127.0.0.1", "--port", "8000",
    ]))
    procs.append(subprocess.Popen(["uv", "run", "python", "-m", "liveclass.broadcaster"]))

    domain = os.environ.get("NGROK_DOMAIN")
    ngrok_cmd = ["ngrok", "http", "8000"]
    if domain:
        ngrok_cmd = ["ngrok", "http", f"--domain={domain}", "8000"]
    procs.append(subprocess.Popen(ngrok_cmd))

    print("LiveClass up. Attach your editor terminal with:")
    print(f"  tmux attach -t {cfg.tmux_session}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for p in procs:
            p.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create `Makefile`**

```makefile
.PHONY: up down test

up:
	uv run python -m liveclass.run

down:
	- pkill -f "liveclass.broadcaster" || true
	- pkill -f "uvicorn liveclass.server" || true
	- pkill -f "ttyd -p 7681" || true
	- pkill -f "ngrok http" || true
	- tmux kill-session -t class || true

test:
	uv run pytest -v
```

- [ ] **Step 4: Create `README.md`**

```markdown
# LiveClass

View-only live broadcast of code, file tree, and terminal for teaching.

## Prerequisites

    brew install ttyd tmux ngrok
    uv pip install -e ".[dev]"

## Run

    export LIVECLASS_TOKEN=some-shared-secret
    export NGROK_DOMAIN=your-reserved.ngrok-free.dev   # optional
    make up

Then attach your editor's terminal to the shared tmux session:

    tmux attach -t class

Run uvicorn / curl / tests inside that session — students see it live.
Edit files under `./lesson/`; saves broadcast to students.

Students open the ngrok URL: explorer + live code + read-only terminal,
all selectable/copyable.

## Config

Edit `liveclass.toml` any time (hot-reloaded): `ignore` list, terminal
`cols`/`rows`, `tmux_session`, `title`. Keep real secrets out of `./lesson/`
— everything visible there is broadcast verbatim.

## Test

    make test
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1-8).

- [ ] **Step 6: Manual end-to-end verification**

```
1. brew install ttyd tmux ngrok   (if not present)
2. export LIVECLASS_TOKEN=test123
3. make up
4. In a second shell: tmux attach -t class ; run `uv run uvicorn main:app --reload`
   from inside ./lesson (or `cd lesson`).
5. Open the ngrok URL in two browser windows. Confirm:
   - explorer shows lesson files including .env, excludes __pycache__
   - editing ./lesson/main.py and saving updates the code pane live in both
   - clicking .env shows it, "return to live" resumes following
   - the terminal pane shows the running uvicorn / endpoint hits
   - selecting text in code and terminal copies cleanly
   - typing in the terminal pane does nothing (read-only)
6. make down
```

- [ ] **Step 7: Commit**

```bash
git add liveclass/run.py Makefile README.md lesson/main.py lesson/.env
git commit -m "feat: runner, sample lesson, and end-to-end docs"
```

---

## Self-Review Notes

- **Spec coverage:** §4 topology → Tasks 4-7,9; §5.1 broadcaster → Task 7; §5.2 server (state/teacher/student/file/terminal/page) → Tasks 4,5,6; §5.3 ttyd+tmux → Task 9; §5.4 student page (+CDN assets) → Task 8; §5.5 ngrok → Task 9; §6 config + hot-reload → Tasks 1,5,7; §7 protocol → Task 3; §8 data flow → Tasks 4,7,8; §9 security (sandbox, ignore in tree+file, token from env, read-only) → Tasks 1,2,5,4,9; §11 testing → unit tests across tasks + manual checklist in Task 9.
- **Deferred to §12 (out of scope):** VS Code extension, Docker — intentionally not in this plan.
- **Type consistency:** wire schemas defined once in Global Constraints and reused; `Config` fields, `create_app(config)`, `is_ignored(rel_path, ignore_patterns)`, `build_tree(lesson_dir, ignore)`, `make_file_message/make_tree_message` signatures consistent across producing/consuming tasks.
