import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from liveclass.config import Settings
from liveclass.server import create_app

_INDEX_HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


def test_root_serves_html(tmp_path):
    cfg = Settings(
        _env_file=None, lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<html" in r.text.lower()


def test_page_loads_cdn_assets_and_panes(tmp_path):
    cfg = Settings(
        _env_file=None, lesson_dir=tmp_path, title="T", ignore=[],
        tmux_session="class", cols=100, rows=30, token="secret",
    )
    client = TestClient(create_app(cfg))
    html = client.get("/").text
    assert "cdn.jsdelivr.net/npm/monaco-editor" in html  # Monaco from CDN
    assert 'id="explorer"' in html
    assert 'id="code"' in html
    assert 'src="/terminal/"' in html  # terminal via proxy
    assert "/ws/student" in html


def test_inline_page_javascript_is_syntactically_valid(tmp_path):
    """Guard against page-breaking JS syntax errors in the student page.

    Algorithm:
        Extract every inline <script> block (those without a src attribute)
        from static/index.html, concatenate them, and run `node --check` on the
        result. A syntax error in the inline script silently breaks the entire
        student page (no editor, no tree, no WebSocket) yet leaves the HTML
        substring assertions passing, so this executes the JS parser directly.

    Args:
        tmp_path (Path): pytest fixture for a scratch file.

    Returns:
        None. Fails if node reports a syntax error; skips if node is absent.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available to syntax-check inline page JS")
    html = _INDEX_HTML.read_text()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert blocks, "expected at least one inline <script> block in index.html"
    js_file = tmp_path / "page_inline.js"
    js_file.write_text("\n".join(blocks))
    result = subprocess.run(
        ["node", "--check", str(js_file)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"inline page JS has a syntax error:\n{result.stderr}"
