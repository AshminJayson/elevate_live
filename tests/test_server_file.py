from fastapi.testclient import TestClient

from liveclass.config import Settings
from liveclass.server import create_app


def _setup(tmp_path, monkeypatch, ignore_json="[]"):
    """Build an app whose lesson dir is a subdir and whose config .env is at the
    (temp) project root, then chdir there so /file's load_settings() reads it.

    Returns:
        tuple[TestClient, Path]: the client and the lesson directory.
    """
    lesson = tmp_path / "lesson"
    lesson.mkdir()
    (lesson / "main.py").write_text("print('hi')")
    (lesson / ".env").write_text("API_KEY=changeme")  # lesson content, shown by design
    (lesson / "secret.py").write_text("TOKEN=abc")
    (tmp_path / ".env").write_text(  # central config; distinct from lesson/.env
        "LIVECLASS_TOKEN=secret\n"
        f"LIVECLASS_LESSON_DIR={lesson.as_posix()}\n"
        f"LIVECLASS_IGNORE={ignore_json}\n"
    )
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app(Settings())), lesson


def test_file_serves_content(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    r = client.get("/file", params={"path": "main.py"})
    assert r.status_code == 200
    assert r.text == "print('hi')"


def test_file_serves_dotfile(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    r = client.get("/file", params={"path": ".env"})  # the lesson's .env
    assert r.status_code == 200
    assert "API_KEY" in r.text


def test_file_rejects_traversal_to_config_env(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    # The central config .env (which holds the token) sits above lesson_dir and
    # must never be reachable via path traversal.
    r = client.get("/file", params={"path": "../.env"})
    assert r.status_code == 404


def test_file_rejects_ignored_after_hot_reload(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch, ignore_json="[]")
    assert client.get("/file", params={"path": "secret.py"}).status_code == 200
    # hot-edit the central .env to hide secret.py; /file reloads it per request
    (tmp_path / ".env").write_text(
        "LIVECLASS_TOKEN=secret\n"
        f"LIVECLASS_LESSON_DIR={(tmp_path / 'lesson').as_posix()}\n"
        'LIVECLASS_IGNORE=["secret.py"]\n'
    )
    assert client.get("/file", params={"path": "secret.py"}).status_code == 404
