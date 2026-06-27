"""Message builders and language detection for the broadcast protocol.

Wire messages (JSON over WebSocket), host -> server -> viewers:
    file: {"type": "file", "path": str, "language": str, "content": str}
    tree: {"type": "tree", "tree": list[node], "root": str}
    cursor: {"type": "cursor", "path": str, "line": int, "column": int,
             "anchorLine": int, "anchorColumn": int}
        The host's caret (line/column) and selection anchor (anchorLine/
        anchorColumn) in the active file; all 0-based (VS Code Position
        semantics). Caret-only when (line, column) == (anchorLine, anchorColumn).

View-mode control (server -> viewers AND server -> hosts):
    view_mode: {"type": "view_mode", "mode": "free" | "code" | "terminal"}

Host -> server control (extension trigger; the host terminal hotkey sends the
same message over a one-shot host socket):
    control: {"type": "control", "action": "cycle_view_mode"}
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


def detect_language(path: str) -> str:
    """Map a file path's extension to a Monaco language id.

    Args:
        path (str): file path (POSIX-relative is fine).

    Returns:
        str: Monaco language id, or "plaintext" if the extension is unknown.
    """
    return LANGUAGE_BY_EXT.get(PurePosixPath(path).suffix, "plaintext")


def file_message(path: str, content: str) -> dict:
    """Build a 'file' wire message for the active file.

    Args:
        path (str): POSIX-relative path of the file.
        content (str): full text content of the file.

    Returns:
        dict: {"type": "file", "path": str, "language": str, "content": str}.
    """
    return {"type": "file", "path": path, "language": detect_language(path), "content": content}


def cursor_message(path: str, line: int, column: int, anchor_line: int, anchor_column: int) -> dict:
    """Build a 'cursor' wire message for the host's caret in the active file.

    Positions are 0-based (VS Code Position semantics): line 0 is the first
    line, column 0 is before the first character. The caret is (line, column);
    the selection anchor is (anchor_line, anchor_column). When the two coincide
    there is no selection and only a caret is shown.

    Args:
        path (str): POSIX-relative path of the file the caret is in.
        line (int): 0-based caret line.
        column (int): 0-based caret column (character offset within the line).
        anchor_line (int): 0-based line of the selection anchor.
        anchor_column (int): 0-based column of the selection anchor.

    Returns:
        dict: {"type": "cursor", "path": str, "line": int, "column": int,
            "anchorLine": int, "anchorColumn": int}.
    """
    return {
        "type": "cursor",
        "path": path,
        "line": line,
        "column": column,
        "anchorLine": anchor_line,
        "anchorColumn": anchor_column,
    }


def tree_message(tree: list[dict], root: str = "") -> dict:
    """Build a 'tree' wire message wrapping a file-tree snapshot.

    Args:
        tree (list[dict]): tree nodes from tree.build_tree.
        root (str): display name of the broadcast project root (the source
            directory's basename); shown as the explorer heading. Empty string
            falls back to a generic heading on the client.

    Returns:
        dict: {"type": "tree", "tree": list[node], "root": str}.
    """
    return {"type": "tree", "tree": tree, "root": root}


if __name__ == "__main__":
    print(file_message("main.py", "print('hello')"))
