# LiveClass

View-only live broadcast of a teacher's code, file tree, and terminal to a
browser — no participant cap, all text copyable, students strictly read-only.

## Architecture

All host processes sit behind a single ngrok ingress on `:8000`. A FastAPI
server is the hub; everything else binds to localhost and is reachable only
through it.

```
                         ngrok (:8000, public URL)
                                   │
                          FastAPI hub  (liveclass/server.py)
        ┌───────────────┬──────────────┬────────────────┬──────────────┐
        │ GET /         │ /ws/student  │ GET /file      │ /terminal(/ws)│
        │ student page  │ fan-out      │ sandboxed read │ proxy → ttyd  │
        └───────────────┴──────▲───────┴────────────────┴──────▲────────┘
                               │ tree + file messages          │ (read-only)
                        /ws/teacher (token-gated)         ttyd :7681  (localhost)
                               │                                │  -b /terminal
                        broadcaster (liveclass/broadcaster.py)  └─ tmux attach -r
                               │ watchdog                          │
                          ./lesson/  +  .env  ───────────────► shared tmux session
```

- **Teacher channel** (`/ws/teacher`): a single broadcaster authenticates with
  `LIVECLASS_TOKEN`. It runs a watchdog file-watcher over `lesson_dir` (and
  `.env`) and pushes two message types: `file` (active file content) and
  `tree` (the file-tree snapshot). It reconnects with backoff if the hub
  restarts.
- **Student channel** (`/ws/student`): receive-only. On connect it replays the
  current tree and active file (late-joiner state), then streams updates.
  Nothing a student sends can affect the host.
- **`GET /file?path=`**: serves one lesson file as plain text, sandboxed under
  `lesson_dir` (path-traversal → 404) and filtered by the ignore list, which
  is re-read from `.env` on every request (hot reload).
- **`/terminal` + `/terminal/ws`**: reverse-proxy to a read-only `ttyd`
  (started with base path `-b /terminal`, no `-W`, attaching tmux with `-r`).
- **`GET /`**: the three-pane student page (explorer / Monaco editor / terminal
  iframe). Monaco and xterm.js load from a CDN, never through the tunnel.

**Read-only by construction:** `/ws/student` never mutates state, `ttyd` runs
without write mode and attaches tmux read-only, and the Monaco editor is
`readOnly`. Only `:8000` is exposed; `ttyd` (`:7681`) and the broadcaster bind
localhost.

## Prerequisites

    brew install ttyd tmux ngrok
    uv pip install -e ".[dev]"

## Configuration

All configuration lives in **one** place: a project-root `.env` file (loaded
via pydantic-settings). Copy the template and edit:

    cp .env.example .env
    # then set LIVECLASS_TOKEN to a shared secret

| Key | Default | Purpose |
|-----|---------|---------|
| `LIVECLASS_TOKEN` | _(required)_ | Teacher/broadcaster auth. Empty = no teacher may connect. |
| `LIVECLASS_NGROK_DOMAIN` | _(blank)_ | Reserved ngrok domain; blank uses a random ephemeral URL. |
| `LIVECLASS_LESSON_DIR` | `./lesson` | Directory broadcast to students. |
| `LIVECLASS_TITLE` | `LiveClass` | Student page title. |
| `LIVECLASS_IGNORE` | see `.env.example` | JSON array of patterns hidden from the tree **and** `/file`. |
| `LIVECLASS_TMUX_SESSION` | `class` | Shared tmux session name. |
| `LIVECLASS_COLS` / `LIVECLASS_ROWS` | `100` / `30` | Fixed terminal size. |

- **Exported env vars override `.env`** (e.g. `LIVECLASS_TOKEN=… make up`), so
  CI and one-off overrides keep working.
- **`LIVECLASS_IGNORE` is hot-reloaded** — edit it mid-class and `/file` plus
  the broadcast tree update with no restart. Other keys are read at startup.
- **Two different `.env` files:** the root `./.env` is your real config and is
  git-ignored. `./lesson/.env` is *teaching content* broadcast verbatim to
  students — keep placeholders only there; never put a real token in it.

## Run

    cp .env.example .env          # first time only; set LIVECLASS_TOKEN
    make up

Then attach your editor's terminal to the shared tmux session:

    tmux attach -t class

Run uvicorn / curl / commands inside that session — students see it live.
Edit files under `./lesson/`; saves broadcast to students. `make down` tears
the stack down.

Students open the ngrok URL: explorer + live code + read-only terminal, all
selectable/copyable.

## Access control

The ngrok URL is the only access control. Every student route (`/`,
`/ws/student`, `/file`, `/terminal`) is unauthenticated by design — anyone with
the URL can view the broadcast. `LIVECLASS_TOKEN` gates only the
teacher/broadcaster connection (`/ws/teacher`), not student access. Share the
URL only with your class and treat it as a secret.

## Test

    make test
