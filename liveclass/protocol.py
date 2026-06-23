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
