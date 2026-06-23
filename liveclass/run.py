"""Supervisor: launches tmux, ttyd, the server, the broadcaster, and ngrok.

Algorithm:
    1. Load config; ensure a tmux session sized to cols x rows exists.
    2. Start ttyd (read-only) attached to that session under base path /terminal.
    3. Start uvicorn serving liveclass.server:create_app.
    4. Start the broadcaster (python -m liveclass.broadcaster).
    5. Start ngrok against :8000 using LIVECLASS_NGROK_DOMAIN if set.
    6. Wait; on Ctrl-C, terminate all children.

Configuration comes from env / a project-root .env (see liveclass.config.Settings);
LIVECLASS_TOKEN is required, LIVECLASS_NGROK_DOMAIN and LIVECLASS_NGROK_AUTHTOKEN
are optional (the authtoken authenticates the ngrok agent via NGROK_AUTHTOKEN).
"""

import os
import shutil
import signal
import subprocess
import sys
import time

from liveclass.config import load_settings


def _ensure_tmux(session, cols, rows):
    """Create the tmux session sized cols x rows if it does not exist."""
    exists = subprocess.run(["tmux", "has-session", "-t", session]).returncode == 0
    if not exists:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", str(cols), "-y", str(rows)],
            check=True,
        )


def _terminate_group(proc):
    """Send SIGTERM to proc's entire process group, then wait; escalate to SIGKILL on timeout.

    Algorithm:
        1. Resolve the process group ID from proc.pid via os.getpgid().
        2. Send SIGTERM to the whole group (reaching grandchildren under uv run wrappers).
        3. Wait up to 5 seconds for the process to exit.
        4. If it has not exited, send SIGKILL to the group.
        5. Ignore ProcessLookupError and PermissionError so an already-dead child is silently skipped.

    Args:
        proc (subprocess.Popen): A child process launched with start_new_session=True.

    Returns:
        None
    """
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _require_binaries(names):
    """Check that each named binary is available on PATH; exit if any are missing.

    Algorithm:
        1. For each name, call shutil.which() to test availability.
        2. Collect all names for which which() returns None.
        3. If the missing list is non-empty, call sys.exit() with a message naming the
           missing binaries and a brew install suggestion.

    Args:
        names (list[str]): Binary names to check (e.g. ["tmux", "ttyd", "ngrok"]).

    Returns:
        None. Calls sys.exit() if any binary is missing.
    """
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        joined = " ".join(missing)
        sys.exit(f"Missing required binaries: {joined}. Install with: brew install {joined}")


def main():
    """Start the LiveClass stack: tmux, ttyd, uvicorn, broadcaster, ngrok.

    Loads config and environment; exits with error if LIVECLASS_TOKEN is unset or
    required binaries (tmux, ttyd, ngrok) are not found on PATH.
    Spawns child processes each in their own process group and waits, terminating
    all children (including grandchildren) on Ctrl-C.

    Returns None on normal shutdown; calls sys.exit() on a missing token or missing binaries.
    """
    cfg = load_settings()
    if not cfg.token:
        sys.exit("LIVECLASS_TOKEN must be set (in .env or the environment)")

    _require_binaries(["tmux", "ttyd", "ngrok"])

    _ensure_tmux(cfg.tmux_session, cfg.cols, cfg.rows)

    procs = []
    # ttyd: read-only (no -W), base path /terminal, attach the session read-only.
    procs.append(subprocess.Popen([
        "ttyd", "-p", "7681", "-b", "/terminal", "-i", "127.0.0.1",
        "tmux", "attach", "-r", "-t", cfg.tmux_session,
    ], start_new_session=True))
    procs.append(subprocess.Popen([
        "uv", "run", "uvicorn", "liveclass.server:create_app",
        "--factory", "--host", "127.0.0.1", "--port", "8000",
    ], start_new_session=True))
    procs.append(subprocess.Popen(
        ["uv", "run", "python", "-m", "liveclass.broadcaster"],
        start_new_session=True,
    ))

    domain = cfg.ngrok_domain
    ngrok_cmd = ["ngrok", "http", "8000"]
    if domain:
        ngrok_cmd = ["ngrok", "http", f"--domain={domain}", "8000"]
    # ngrok reads its account credential from NGROK_AUTHTOKEN when set, so a
    # token in .env authenticates the agent without touching ngrok's own config.
    ngrok_env = os.environ.copy()
    if cfg.ngrok_authtoken:
        ngrok_env["NGROK_AUTHTOKEN"] = cfg.ngrok_authtoken
    procs.append(subprocess.Popen(ngrok_cmd, start_new_session=True, env=ngrok_env))

    print("LiveClass up. Attach your editor terminal with:")
    print(f"  tmux attach -t {cfg.tmux_session}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for p in procs:
            _terminate_group(p)


if __name__ == "__main__":
    main()
