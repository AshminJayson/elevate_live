from fastapi.testclient import TestClient

from liveclass.config import Config
from liveclass.server import create_app


def test_root_serves_html(tmp_path):
    cfg = Config(
        lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()
