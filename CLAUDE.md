# CLAUDE.md — BitForge

Project-specific notes. Global preferences live in `~/.claude/CLAUDE.md`.

## Process model & teardown (gotcha)

The supervisor is `python -m bitforge.run` (`make up`). It spawns four children,
each in its **own process group** (`start_new_session=True`):

- `python -m bitforge.serve` — the hub. This runs `uvicorn.run(...)` **in-process**,
  so the process is `bitforge.serve`, **not** a `uvicorn ...` command line.
- `python -m bitforge.broadcaster` — the file watcher / `/ws/host` pusher.
- `ttyd -p 7681 ...` — the read-only terminal proxy.
- `cloudflared tunnel run --token <BITFORGE_CLOUDFLARED_TOKEN>` — the public
  tunnel. A **named** tunnel: the token (required) encodes which tunnel to run
  and its dashboard-configured ingress (public hostname → localhost:8000).
  cloudflared does not emit the public URL, so BitForge echoes
  `BITFORGE_PUBLIC_URL` itself; the tunnel's own output is redirected to the log
  like the other children (no piping/scraping). ngrok has been removed entirely.

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
   and the tunnel (`cloudflared tunnel`), then `tmux kill-session`. See the
   `down` target in the Makefile.

To confirm a clean stop: ports 8000 and 7681 free, no `bitforge.(run|serve|broadcaster)`
processes, and the tmux session gone.
