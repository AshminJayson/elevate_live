"""Tests for broadcaster: file-watcher and tree/file message builders."""

from bitforge.broadcaster import coalesce_burst, make_file_message, make_tree_message


def test_make_file_message(tmp_path):
    """Test building a file message from a text file."""
    (tmp_path / "main.py").write_text("x = 1")
    msg = make_file_message(tmp_path, "main.py", [])
    assert msg == {"type": "file", "path": "main.py", "language": "python", "content": "x = 1"}


def test_make_file_message_ignored_returns_none(tmp_path):
    """Test that ignored files return None."""
    (tmp_path / "a.pyc").write_text("x")
    assert make_file_message(tmp_path, "a.pyc", ["*.pyc"]) is None


def test_make_file_message_missing_returns_none(tmp_path):
    """Test that missing files return None."""
    assert make_file_message(tmp_path, "nope.py", []) is None


def test_make_file_message_binary_returns_none(tmp_path):
    """A non-UTF-8 (binary) file is skipped (returns None)."""
    (tmp_path / "img.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
    assert make_file_message(tmp_path, "img.bin", []) is None


def test_make_tree_message(tmp_path):
    """Test building a tree message from a directory."""
    (tmp_path / "main.py").write_text("x")
    msg = make_tree_message(tmp_path, [])
    assert msg["type"] == "tree"
    assert msg["tree"][0]["path"] == "main.py"


def test_coalesce_burst_keeps_file_after_leading_tree_event():
    """An atomic save (write-temp + rename) emits a burst that leads with a
    create ('tree') event and ends with the real file modify; the file path
    must survive coalescing so its content is still broadcast."""
    burst = [
        ("tree", "main.py.tmp"),
        ("file", "main.py.tmp"),
        ("tree", "main.py.tmp"),
        ("file", "main.py"),
    ]
    assert coalesce_burst(burst) == ["main.py.tmp", "main.py"]


def test_coalesce_burst_dedups_keeping_latest_occurrence():
    """A file edited twice in one burst is sent once, ordered by last touch so
    the most recently changed file is the active (last-sent) one."""
    assert coalesce_burst([("file", "a.py"), ("file", "b.py"), ("file", "a.py")]) == ["b.py", "a.py"]


def test_coalesce_burst_drops_tree_only_and_empty_paths():
    """Tree-only bursts (creates/deletes/moves) yield no file resends."""
    assert coalesce_burst([("tree", ""), ("tree", "pkg")]) == []
