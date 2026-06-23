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
        list[dict]: tree nodes, each with schema:
            {"name": str, "path": str, "type": "file"|"dir", "children": list[dict]?}
            where "path" is POSIX-relative to lesson_dir and "children" is present
            only when type == "dir".
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
