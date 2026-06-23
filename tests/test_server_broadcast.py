import pytest
from fastapi.testclient import TestClient

from liveclass.config import Settings
from liveclass.server import create_app


def _config(tmp_path, token="secret"):
    return Settings(
        _env_file=None,
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


def test_teacher_empty_config_token_rejected(tmp_path):
    app = create_app(_config(tmp_path, token=""))
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/teacher?token="):
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
