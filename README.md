# BitForge

View-only live broadcast of a host's code, file tree, and terminal to a
browser — no participant cap, all text copyable, viewers strictly read-only.

## Architecture

All host processes sit behind a single public tunnel on `:8000` — **ngrok** by
default, or a **cloudflared** quick tunnel when `BITFORGE_CLOUDFLARED_TOKEN` is
set. A FastAPI server is the hub; everything else binds to localhost and is
reachable only through it.

```
                  ngrok | cloudflared  (:8000, public URL)
                                   │
                          FastAPI hub  (bitforge/server.py)
        ┌───────────────┬──────────────┬────────────────┬──────────────┐
        │ GET /         │ /ws/viewer   │ GET /file      │ /terminal(/ws)│
        │ viewer page   │ fan-out      │ sandboxed read │ proxy → ttyd  │
        └───────────────┴──────▲───────┴────────────────┴──────▲────────┘
                               │ tree + file messages          │ (read-only)
                        /ws/host (token-gated)            ttyd :7681  (localhost)
                               │                                │  -b /terminal
                        broadcaster (bitforge/broadcaster.py)  └─ tmux attach -r
                               │ watchdog                          │
                          ./source/  +  .env  ───────────────► shared tmux session
```

- **Host channel** (`/ws/host`): a single broadcaster authenticates with
  `BITFORGE_TOKEN`. It runs a watchdog file-watcher over `source_dir` (and
  `.env`) and pushes two message types: `file` (active file content) and
  `tree` (the file-tree snapshot). It reconnects with backoff if the hub
  restarts.
- **Viewer channel** (`/ws/viewer`): receive-only. On connect it replays the
  current tree and active file (late-joiner state), then streams updates.
  Nothing a viewer sends can affect the host.
- **`GET /file?path=`**: serves one source file as plain text, sandboxed under
  `source_dir` (path-traversal → 404) and filtered by the ignore list, which
  is re-read from `.env` on every request (hot reload).
- **`/terminal` + `/terminal/ws`**: reverse-proxy to a read-only `ttyd`
  (started with base path `-b /terminal`, no `-W`, attaching tmux with `-r`).
- **`GET /`**: the three-pane viewer page (explorer / Monaco editor / terminal
  iframe). Monaco and xterm.js load from a CDN, never through the tunnel.

**Read-only by construction:** `/ws/viewer` never mutates state, `ttyd` runs
without write mode and attaches tmux read-only, and the Monaco editor is
`readOnly`. Only `:8000` is exposed; `ttyd` (`:7681`) and the broadcaster bind
localhost.

## Repository layout

```
bitforge/            FastAPI hub + broadcaster (the Python package)
  server.py          hub: viewer page, /ws/{host,viewer}, /file, /terminal proxy
  serve.py           runnable server entry: uvicorn with split console/file logging
  broadcaster.py     watchdog file-watcher → tree + active-file messages
  tree.py            build the JSON file-tree of the source dir (ignore-aware)
  protocol.py        wire-message builders + extension→language mapping
  config.py          pydantic-settings (.env, BITFORGE_* keys)
  logging_setup.py   dictConfig routing uvicorn → log file, heartbeat → console
  run.py             supervisor: starts tmux, ttyd, the server, broadcaster, tunnel
static/index.html    the three-pane viewer page (explorer / Monaco / terminal)
extension/           VS Code "Live Sync" extension (streams the unsaved buffer)
source/              default source directory broadcast to viewers
tests/               pytest suite (run with `make test`)
.env.example         configuration template (copy to .env)
```

The explorer heading shown to viewers is the **basename of the broadcast
source directory** (`BITFORGE_SOURCE_DIR`), so viewers see the name of the
project you are sharing rather than a generic label.

## Prerequisites

    brew install ttyd tmux ngrok        # ngrok is the default tunnel
    brew install cloudflared            # only if you set BITFORGE_CLOUDFLARED_TOKEN
    uv pip install -e ".[dev]"

## Configuration

All configuration lives in **one** place: a project-root `.env` file (loaded
via pydantic-settings). Copy the template and edit:

    cp .env.example .env
    # then set BITFORGE_TOKEN to a shared secret

| Key | Default | Purpose |
|-----|---------|---------|
| `BITFORGE_TOKEN` | _(required)_ | Host/broadcaster auth. Empty = no host may connect. |
| `BITFORGE_NGROK_DOMAIN` | _(blank)_ | Reserved ngrok domain; blank uses a random ephemeral URL. |
| `BITFORGE_NGROK_AUTHTOKEN` | _(blank)_ | ngrok account credential (passed as `NGROK_AUTHTOKEN`); blank falls back to your `ngrok config`. |
| `BITFORGE_CLOUDFLARED_TOKEN` | _(blank)_ | Any non-empty value switches the tunnel from ngrok to a cloudflared quick tunnel (value is only an on/off switch). |
| `BITFORGE_SOURCE_DIR` | `./source` | Directory broadcast to viewers. |
| `BITFORGE_TITLE` | `BitForge` | Viewer page title. |
| `BITFORGE_IGNORE` | see `.env.example` | JSON array of patterns hidden from the tree **and** `/file`. |
| `BITFORGE_TMUX_SESSION` | `class` | Shared tmux session name. |
| `BITFORGE_COLS` / `BITFORGE_ROWS` | `100` / `30` | Initial terminal size (host's `tmux attach` drives the live size). |
| `BITFORGE_LOG_FILE` | `bitforge.log` | Detailed append-mode log file; the console stays quiet except the heartbeat and the public URL. |
| `BITFORGE_HEARTBEAT_SECONDS` | `10` | Interval between console "viewers online" heartbeat lines. |

- **Exported env vars override `.env`** (e.g. `BITFORGE_TOKEN=… make up`), so
  CI and one-off overrides keep working.
- **The tunnel is chosen by which credential is set:** leave
  `BITFORGE_CLOUDFLARED_TOKEN` blank to use ngrok (the default), or set it to
  any non-empty value to use a cloudflared quick tunnel instead. Either way the
  public URL is printed to the console (and the log) the moment it appears.
- **`BITFORGE_IGNORE` is hot-reloaded** — edit it mid-session and `/file` plus
  the broadcast tree update with no restart. Other keys are read at startup.
- **Two different `.env` files:** the root `./.env` is your real config and is
  git-ignored. `./source/.env` is *broadcast content* served verbatim to
  viewers — keep placeholders only there; never put a real token in it.

## Run

    cp .env.example .env          # first time only; set BITFORGE_TOKEN
    make up

Then attach your editor's terminal to the shared tmux session:

    tmux attach -t class

Run uvicorn / curl / commands inside that session — viewers see it live.
Edit files under `./source/`; saves broadcast to viewers. `make down` tears
the stack down.

`make up` prints the public tunnel URL to the console once it is ready. Viewers
open that URL: explorer + live code + read-only terminal, all
selectable/copyable.

### Follow-along typing (no save) — VS Code extension

The filesystem watcher only reacts to **saves**. To have viewers follow your
typing keystroke-by-keystroke, install the bundled VS Code extension in
[`extension/`](extension/README.md): it streams the active editor's *unsaved*
buffer to the hub over `/ws/host`. The server validates the path (sandbox +
ignore) and derives the language, then fans it out to viewers like any other
file update. The broadcaster still owns the file tree and the saved-file
fallback, so the two run together.

### Terminal sizing and views

- **You drive the terminal size.** The tmux session uses `window-size largest`,
  so your interactive `tmux attach` sets the size and read-only viewers
  never shrink or grow it (and a new viewer no longer reflows everyone's view).
  `BITFORGE_COLS`/`ROWS` are the initial size only.
- **Viewers can scroll.** ttyd runs with a 10k-line scrollback buffer.
- **View mode is host-controlled.** A single `view_mode` cycles
  `free → code → terminal`: in **free** each viewer toggles their own layout
  (hide the explorer + code panes with the `terminal` toggle, or visit
  `…/#terminal`); in **code** and **terminal** the host forces every viewer's
  layout and disables their toggle. Cycle it with the `t` key in the hub's
  terminal, or the **BitForge: Cycle View Mode** command / status-bar item in
  the extension.

## Access control

The tunnel URL is the only access control. Every viewer route (`/`,
`/ws/viewer`, `/file`, `/terminal`) is unauthenticated by design — anyone with
the URL can view the broadcast. `BITFORGE_TOKEN` gates only the
host/broadcaster connection (`/ws/host`), not viewer access. Share the
URL only with your audience and treat it as a secret.

## Test

    make test

## Make targets

| Target | Action |
|--------|--------|
| `make up` | Start the full stack (tmux, ttyd, the hub, broadcaster, and the ngrok/cloudflared tunnel). |
| `make down` | Tear it all down (both tunnels included) and kill the shared tmux session. |
| `make test` | Run the pytest suite. |

## License

[MIT](LICENSE) © Ashmin Jayson. The bundled VS Code extension under
[`extension/`](extension/) is MIT-licensed as well.
