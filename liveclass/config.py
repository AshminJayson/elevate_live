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
