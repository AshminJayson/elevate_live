# CLAUDE.md — BitForge

Project-specific notes. Global preferences live in `~/.claude/CLAUDE.md`.

## Process model & teardown (gotcha)

The supervisor is `python -m bitforge.run` (`make up`). It spawns four children,
each in its **own process group** (`start_new_session=True`):

- `python -m bitforge.serve` — the hub. This runs `uvicorn.run(...)` **in-process**,
  so the process is `bitforge.serve`, **not** a `uvicorn ...` command line.
- `python -m bitforge.broadcaster` — the file watcher / `/ws/host` pusher.
- `ttyd -p 7681 ...` — the read-only terminal proxy.
- `ngrok http 8000` — the public tunnel.

Two consequences for anything that stops or matches these processes (`make down`,
scripts, `pkill`, monitoring):

1. **Match the server as `bitforge.serve`, never `uvicorn`.** `pkill -f "uvicorn …"`
   silently matches nothing. This was a real `make down` bug (fixed): it left the
   server holding port 8000.
2. **Kill each child by name; killing `bitforge.run` alone is not enough.** The
   supervisor only tears down its children in a `finally` reached on
   `KeyboardInterrupt` (Ctrl-C). A `SIGTERM` (what `pkill` sends) terminates it
   **without** running that `finally`, orphaning the children. So teardown must
   `pkill` `bitforge.run`, `bitforge.serve`, `bitforge.broadcaster`, `ttyd -p 7681`,
   and `ngrok http`, then `tmux kill-session`. See the `down` target in the Makefile.

To confirm a clean stop: ports 8000 and 7681 free, no `bitforge.(run|serve|broadcaster)`
processes, and the tmux session gone.
