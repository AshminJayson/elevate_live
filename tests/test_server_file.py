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
