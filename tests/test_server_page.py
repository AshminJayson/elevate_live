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


def test_page_loads_cdn_assets_and_panes(tmp_path):
    cfg = Config(
        lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    html = client.get("/").text
    assert "cdn.jsdelivr.net/npm/monaco-editor" in html  # Monaco from CDN
    assert 'id="explorer"' in html
    assert 'id="code"' in html
    assert 'src="/terminal/"' in html  # terminal via proxy
    assert "/ws/student" in html
