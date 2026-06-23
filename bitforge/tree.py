"""Build a JSON-serializable file tree of the source directory."""

from pathlib import Path

from bitforge.config import is_ignored


def build_tree(source_dir, ignore):
    """Walk source_dir into a nested list of tree nodes, honoring ignore.

    Algorithm:
        Recurse each directory. Within a directory, sort entries so dirs come
        before files and each group is alphabetical (case-insensitive). Skip
        any entry whose POSIX-relative path is ignored. Directory nodes carry
        a "children" list; file nodes do not.

    Args:
        source_dir (str | Path): root directory to walk.
        ignore (list[str]): ignore patterns (see config.is_ignored).

    Returns:
        list[dict]: tree nodes, each with schema:
            {"name": str, "path": str, "type": "file"|"dir", "children": list[dict]?}
            where "path" is POSIX-relative to source_dir and "children" is present
            only when type == "dir".
    """
    root = Path(source_dir)

    def walk(directory):
        """Recursively build sorted tree nodes for one directory, skipping ignored paths.

        Args:
            directory (Path): directory to list.

        Returns:
            list[dict]: tree nodes for this level (dirs before files, each alphabetical).
        """
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

    print(json.dumps(build_tree("source", []), indent=2))
