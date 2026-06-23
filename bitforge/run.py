"""Supervisor: launches tmux, ttyd, the server, the broadcaster, and ngrok.

Algorithm:
    1. Load config; ensure a tmux session sized to cols x rows exists.
    2. Start ttyd (read-only) attached to that session under base path /terminal.
    3. Start the server (python -m bitforge.serve) with split console/file logging.
    4. Start the broadcaster (python -m bitforge.broadcaster).
    5. Start ngrok against :8000 using BITFORGE_NGROK_DOMAIN if set.
    6. Wait; on Ctrl-C, terminate all children.

The console is kept quiet: ttyd, the broadcaster, and ngrok have their output
redirected into the detailed log file (cfg.log_file), and only the server
inherits this terminal so its periodic "viewers online" heartbeat shows.

Configuration comes from env / a project-root .env (see bitforge.config.Settings);
BITFORGE_TOKEN is required, BITFORGE_NGROK_DOMAIN and BITFORGE_NGROK_AUTHTOKEN
are optional (the authtoken authenticates the ngrok agent via NGROK_AUTHTOKEN).
"""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

from bitforge.config import load_settings


def _ensure_tmux(session, cols, rows, start_dir):
    """Create the tmux session at an initial cols x rows, with the host driving size.

    Algorithm:
        1. Create the session detached at cols x rows if it does not exist (a sane
           default size for viewers before anyone attaches interactively), rooted
           at start_dir via `-c` so the terminal opens in the broadcast project
           rather than wherever `make up` happened to be launched. The flag only
           applies on creation; an existing session keeps its own directory.
        2. Set window-size to 'largest'. Verified behavior: the interactive
           read-write client (the host's own `tmux attach`) drives the window
           size, while read-only ttyd viewer clients never shrink OR grow it.
           So the host's terminal is whatever size they want, and a new
           viewer joining no longer reflows ("splits") everyone else's view.
           (cols/rows are therefore the initial size only, not a hard lock.)

    Args:
        session (str): tmux session name.
        cols (int): initial terminal width.
        rows (int): initial terminal height.
        start_dir (Path | str): absolute directory the session starts in
            (config.source_dir), so the viewed terminal matches the broadcast
            file tree. tmux fails loudly if it does not exist.

    Returns:
        None
    """
    exists = subprocess.run(["tmux", "has-session", "-t", session]).returncode == 0
    if not exists:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session,
             "-x", str(cols), "-y", str(rows), "-c", str(start_dir)],
            check=True,
        )
    subprocess.run(["tmux", "set-option", "-t", session, "window-size", "largest"], check=True)


def _ttyd_cmd(session):
    """Build the ttyd argv for a read-only, scrollable terminal under /terminal.

    Algorithm:
        Start from the fixed flags (port 7681, base path /terminal, localhost
        bind) then append xterm.js client options via repeated `-t key=value`:
        a real scrollback buffer (viewers can scroll back through output, which
        the default has none of), no leave-alert (the page is an iframe), a
        legible font, and a theme matching the BitForge dark chrome. Finish with
        the read-only tmux attach (`-r`), the source of the view.

    Args:
        session (str): tmux session name to attach read-only.

    Returns:
        list[str]: the ttyd command argv.
    """
    options = [
        "scrollback=10000",
        "disableLeaveAlert=true",
        "fontSize=14",
        "fontFamily=ui-monospace, SFMono-Regular, Menlo, monospace",
        'theme={"background":"#0E0E10","foreground":"#E8E6E3"}',
    ]
    cmd = ["ttyd", "-p", "7681", "-b", "/terminal", "-i", "127.0.0.1"]
    for opt in options:
        cmd += ["-t", opt]
    cmd += ["tmux", "attach", "-r", "-t", session]
    return cmd


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


def _cycle_view_mode(token):
    """Tell the running hub to cycle the viewer view mode, via a one-shot host socket.

    The hub server (bitforge.serve) runs in its own session and cannot read this
    terminal, so the supervisor — which owns the foreground TTY — carries the
    hotkey and reaches the server over the network. It opens a short-lived
    authenticated host WebSocket to the local hub, sends a single
    cycle_view_mode control message, and closes. Best-effort: any failure is
    printed to the supervisor console rather than raised.

    Args:
        token (str): host auth token (config.token) gating /ws/host.

    Returns:
        None.
    """
    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:8000/ws/host?token={token}"
    try:
        with connect(url, open_timeout=2) as ws:
            ws.send(json.dumps({"type": "control", "action": "cycle_view_mode"}))
    except Exception as exc:  # the hotkey is best-effort; surface, do not crash
        print(f"[hotkey] view-mode cycle failed: {exc!r}")


def main():
    """Start the BitForge stack: tmux, ttyd, uvicorn, broadcaster, ngrok.

    Loads config and environment; exits with error if BITFORGE_TOKEN is unset or
    required binaries (tmux, ttyd, ngrok) are not found on PATH.
    Spawns child processes each in their own process group and waits, terminating
    all children (including grandchildren) on Ctrl-C.

    Returns None on normal shutdown; calls sys.exit() on a missing token or missing binaries.
    """
    cfg = load_settings()
    if not cfg.token:
        sys.exit("BITFORGE_TOKEN must be set (in .env or the environment)")

    _require_binaries(["tmux", "ttyd", "ngrok"])

    _ensure_tmux(cfg.tmux_session, cfg.cols, cfg.rows, cfg.source_dir)

    # One append-mode log file collects the firehose from the noisy children
    # (ttyd, broadcaster, ngrok) so the host's console stays clean. The
    # server (bitforge.serve) writes its own detailed records to this same file
    # via Python logging and keeps the console for the heartbeat only, so it
    # alone inherits this terminal's stdout/stderr.
    log_path = Path(cfg.log_file).resolve()
    logf = open(log_path, "a", buffering=1)
    quiet = {"stdout": logf, "stderr": subprocess.STDOUT}

    procs = []
    # ttyd: read-only (no -W), base path /terminal, scrollable, themed (see _ttyd_cmd).
    procs.append(subprocess.Popen(_ttyd_cmd(cfg.tmux_session), start_new_session=True, **quiet))
    procs.append(subprocess.Popen(
        ["uv", "run", "python", "-m", "bitforge.serve"],
        start_new_session=True,
    ))
    procs.append(subprocess.Popen(
        ["uv", "run", "python", "-m", "bitforge.broadcaster"],
        start_new_session=True, **quiet,
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
    procs.append(subprocess.Popen(ngrok_cmd, start_new_session=True, env=ngrok_env, **quiet))

    print("BitForge up. Attach your editor terminal with:")
    print(f"  tmux attach -t {cfg.tmux_session}")
    print(f"Logs: {log_path}  (console shows viewers online every {cfg.heartbeat_seconds}s)")

    # Hotkey: while this terminal is interactive, press 't' to cycle the viewer
    # view mode (free -> code -> terminal). cbreak gives single-keypress reads
    # while leaving ISIG on, so Ctrl-C still raises KeyboardInterrupt. When stdin
    # is not a TTY (piped/detached) the hotkey is disabled and we just idle.
    hotkey = sys.stdin.isatty()
    old_termios = None
    if hotkey:
        print("Press 't' here to cycle the viewer view mode (free -> code -> terminal).")
        old_termios = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
    try:
        while True:
            if hotkey:
                if select.select([sys.stdin], [], [], 1)[0]:
                    ch = sys.stdin.read(1)
                    if ch == "":
                        hotkey = False  # stdin closed (EOF): stop polling, just idle
                    elif ch == "t":
                        _cycle_view_mode(cfg.token)
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if old_termios is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_termios)
        for p in procs:
            _terminate_group(p)
        logf.close()


if __name__ == "__main__":
    main()
