"""Runnable server entry: uvicorn with BitForge's split console/file logging.

The supervisor (bitforge.run) launches this instead of the bare `uvicorn` CLI so
that uvicorn adopts our logging contract (see bitforge.logging_setup): its own
startup banner and access lines go to the log file, while the only thing that
reaches this process's stdout — which the host's console inherits — is the
periodic heartbeat. The detailed log file is the same one the supervisor points
its other children at, so all history lands in one place.
"""

from pathlib import Path

import uvicorn

from bitforge.config import load_settings
from bitforge.logging_setup import build_log_config
from bitforge.server import create_app


def main():
    """Resolve config, then run uvicorn on 127.0.0.1:8000 with the split log config.

    Algorithm:
        1. Load settings to find the log file path (resolved to absolute so this
           process and the supervisor write to the exact same file regardless of
           cwd) and build the dictConfig that routes uvicorn -> file, heartbeat
           -> console.
        2. Run uvicorn against the create_app factory with that log_config; the
           app's lifespan starts the heartbeat task.

    Returns:
        None.
    """
    cfg = load_settings()
    log_path = str(Path(cfg.log_file).resolve())
    uvicorn.run(
        "bitforge.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_config=build_log_config(log_path),
    )


if __name__ == "__main__":
    main()
