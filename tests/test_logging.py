"""Tests for log routing config and the new logging-related settings.

These cover the pure, in-process pieces of the observability work:
  - the new Settings fields (log_file, heartbeat_seconds) and their defaults,
  - build_log_config's routing contract (uvicorn noise -> file only; the
    host-facing console logger -> console; detailed events -> file only).

The end-to-end console-cleanliness and concurrent-count behavior is verified
against a real running stack (see the supervisor), not here.
"""

import logging
import time

from fastapi.testclient import TestClient

from bitforge.config import Settings
from bitforge.logging_setup import build_log_config
from bitforge.server import create_app


def test_settings_has_log_defaults():
    """A default config names a log file and a 10s console heartbeat."""
    cfg = Settings(_env_file=None)
    assert cfg.log_file == "bitforge.log"
    assert cfg.heartbeat_seconds == 10


def test_uvicorn_logs_routed_to_file_only():
    """uvicorn's own loggers must not reach the console (keeps it clean)."""
    cfg = build_log_config("/tmp/bitforge.log")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert cfg["loggers"][name]["handlers"] == ["file"]


def test_console_logger_reaches_console_and_file():
    """The host-facing heartbeat logger writes to console AND is recorded."""
    cfg = build_log_config("/tmp/bitforge.log")
    handlers = cfg["loggers"]["bitforge.console"]["handlers"]
    assert "console" in handlers
    assert "file" in handlers


def test_event_logger_is_file_only():
    """Detailed connect/disconnect events are recorded but never spam console."""
    cfg = build_log_config("/tmp/bitforge.log")
    assert cfg["loggers"]["bitforge.events"]["handlers"] == ["file"]


def test_file_handler_targets_given_path():
    """The file handler writes to the path it was built with."""
    cfg = build_log_config("/var/log/forge.log")
    assert cfg["handlers"]["file"]["filename"] == "/var/log/forge.log"


class _Capture(logging.Handler):
    """Collects formatted log messages for assertions."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_heartbeat_reports_viewers_online(tmp_path):
    """While a viewer is connected, the console heartbeat emits an online count.

    Uses the app lifespan (entered via `with TestClient(...)`) to start the
    heartbeat at a fast interval, attaches a capturing handler to the
    bitforge.console logger, connects one viewer, and waits for a tick.
    """
    cfg = Settings(_env_file=None, source_dir=tmp_path, token="secret", heartbeat_seconds=1)
    cap = _Capture()
    console = logging.getLogger("bitforge.console")
    console.addHandler(cap)
    console.setLevel(logging.INFO)
    try:
        with TestClient(create_app(cfg)) as client:
            with client.websocket_connect("/ws/viewer") as viewer:
                viewer.receive_json()  # drain initial tree
                # The heartbeat fires once per second; poll a moment for a tick.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not cap.messages:
                    time.sleep(0.1)
    finally:
        console.removeHandler(cap)
    assert any("online=1" in m for m in cap.messages), cap.messages
