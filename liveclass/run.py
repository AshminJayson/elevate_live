"""Supervisor: launches tmux, ttyd, the server, the broadcaster, and ngrok.

Algorithm:
    1. Load config; ensure a tmux session sized to cols x rows exists.
    2. Start ttyd (read-only) attached to that session under base path /terminal.
    3. Start uvicorn serving liveclass.server:create_app.
    4. Start the broadcaster (python -m liveclass.broadcaster).
    5. Start ngrok against :8000 using NGROK_DOMAIN if set.
    6. Wait; on Ctrl-C, terminate all children.

Env:
    LIVECLASS_TOKEN: required teacher token.
    LIVECLASS_CONFIG: config path (default liveclass.toml).
    NGROK_DOMAIN: reserved static ngrok domain (optional).
"""

import os
import signal
import subprocess
import sys
import time

from liveclass.config import load_config


def _ensure_tmux(session, cols, rows):
    """Create the tmux session sized cols x rows if it does not exist."""
    exists = subprocess.run(["tmux", "has-session", "-t", session]).returncode == 0
    if not exists:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows)],
            check=True,
        )


def main():
    """Start the LiveClass stack: tmux, ttyd, uvicorn, broadcaster, ngrok.

    Loads config and environment; exits with error if LIVECLASS_TOKEN is unset.
    Spawns child processes and waits, terminating all on Ctrl-C.
    """
    config_path = os.environ.get("LIVECLASS_CONFIG", "liveclass.toml")
    cfg = load_config(config_path)
    if not cfg.token:
        sys.exit("LIVECLASS_TOKEN must be set")

    _ensure_tmux(cfg.tmux_session, cfg.cols, cfg.rows)

    procs = []
    # ttyd: read-only (no -W), base path /terminal, attach the session read-only.
    procs.append(subprocess.Popen([
        "ttyd", "-p", "7681", "-b", "/terminal", "-i", "127.0.0.1",
        "tmux", "attach", "-r", "-t", cfg.tmux_session,
    ]))
    procs.append(subprocess.Popen([
        "uv", "run", "uvicorn", "liveclass.server:create_app",
        "--factory", "--host", "127.0.0.1", "--port", "8000",
    ]))
    procs.append(subprocess.Popen(["uv", "run", "python", "-m", "liveclass.broadcaster"]))

    domain = os.environ.get("NGROK_DOMAIN")
    ngrok_cmd = ["ngrok", "http", "8000"]
    if domain:
        ngrok_cmd = ["ngrok", "http", f"--domain={domain}", "8000"]
    procs.append(subprocess.Popen(ngrok_cmd))

    print("LiveClass up. Attach your editor terminal with:")
    print(f"  tmux attach -t {cfg.tmux_session}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for p in procs:
            p.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    main()
