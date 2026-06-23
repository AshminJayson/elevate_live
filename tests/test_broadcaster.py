"""Tests for broadcaster: file-watcher and tree/file message builders."""

from liveclass.broadcaster import make_file_message, make_tree_message


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


def test_make_tree_message(tmp_path):
    """Test building a tree message from a directory."""
    (tmp_path / "main.py").write_text("x")
    msg = make_tree_message(tmp_path, [])
    assert msg["type"] == "tree"
    assert msg["tree"][0]["path"] == "main.py"
