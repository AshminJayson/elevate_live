# LiveClass — Design Spec

**Date:** 2026-06-23
**Status:** Approved for planning

---

## 1. Goal

Let 60+ students (no upper limit) watch a backend/FastAPI class **live in their
browser** — the teacher's code, file structure, and running system — and
**select/copy any text**. View-only: no editing, no student cursors. Free, self-hosted,
unlimited viewers.

The class is a *text medium* (code, terminal, logs, HTTP responses). Streaming text —
not video — is what makes it copyable, crisp at any size, tiny on bandwidth, and
uncapped. This is the core premise the whole design protects.

## 2. Non-goals

- **No collaboration.** One-way broadcast only. (This is *why* there's no participant
  cap: tools like Live Share/CodeTogether cap at 30/50 because of bidirectional sync;
  a view-only fan-out has no such cost.)
- **No literal GUI pixel mirror.** We do not screen-share the VS Code window as video —
  that would forfeit copyable, crisp text. The explorer is *reconstructed from data*
  (Section 6), not streamed as pixels.
- **No Docker.** All host processes; containers added only the terminal-namespace
  problem and bought only reproducibility, which is not a requirement here.

## 3. Key decisions and rationale

| Decision | Why |
|---|---|
| Text broadcast, not video | Copyable + crisp + tiny bandwidth + uncapped. Video loses all four. |
| Teach in VS Code, broadcast VS Code | Match the *students'* mental model. Terminal-only (tmux+nvim) is elegant but foreign to GUI-oriented students — a constant translation tax. |
| Terminal is first-class | Backend students must learn to read logs/tracebacks/endpoint hits. Shown inside a VS Code-like frame, the terminal *closes* that gap instead of widening it. |
| Explorer reconstructed as JSON, not pixels | Keeps everything copyable; lets students browse files at their own pace — better than watching the literal screen. |
| Non-Docker, host processes | Removes the container terminal-namespace problem entirely; ttyd attaches the host tmux trivially. |
| One ingress via reverse-proxy + single tunnel | The "only one thing exposed" property comes from proxying ttyd through the server and tunneling only `:8000` — not from Docker. |
| `liveclass.toml` as single source of exclusions | Hot-editable mid-class; enforced in both tree generation and `/file`. |

## 4. Architecture

```
   You (host VS Code + host terminal)
     │ edit / add / rename files        │ type commands
     ▼                                   ▼
  ./lesson/  (project dir)            tmux session "class"
     │ fs events (content + tree)        │ run uvicorn/curl/tests
     ▼                                   ▼ read-only attach
  broadcaster                         ttyd  :7681 (localhost)
     │ tree + file JSON (WS out)         │ terminal stream
     ▼                                   │
  ┌──────────────────────────────────────┼─────────────────────────┐
  │             broadcast-server (FastAPI)  :8000                    │
  │   WS /ws/teacher?token=…   ← broadcaster (authed)               │
  │   WS /ws/student           → fan-out tree + file                │
  │   WS /terminal             ←→ proxy to ttyd                     │
  │   GET /file?path=…         → read-only file (sandboxed)          │
  │   GET /                    → student page                        │
  │   state: current_tree + current_file + last_content             │
  └──────────────────────────────────┬──────────────────────────────┘
                                      │ :8000 ONLY
                                      ▼
                                  ngrok ──► students (one static URL)
```

Only `:8000` is tunneled. `ttyd` and the broadcaster bind to localhost and surface to
students *through* the server. One ingress, everything flows through it.

## 5. Components (each isolated, one job)

### 5.1 broadcaster (`broadcaster.py`)
- **Does:** watches `./lesson/`. On modify/save → sends a `file` message (and marks it
  current). On add/delete/rename → sends a `tree` message. Watches `liveclass.toml`;
  on change → re-walks with the new ignore list and re-broadcasts the tree.
- **Interface:** outbound WebSocket client to `ws://localhost:8000/ws/teacher?token=…`.
  ~100ms debounce to coalesce rapid auto-saves.
- **Depends on:** `watchdog`, `lesson_dir`, the token (env), `liveclass.toml`.

### 5.2 broadcast-server (`server.py`) — the hub
- **Does:** authenticates the teacher socket, holds state, fans out to students, serves
  the page, proxies the terminal, serves files on request. Watches `liveclass.toml`
  and re-applies the ignore list to `/file`.
- **State (in memory, single source of truth):** `current_tree`, `current_file`,
  `last_content`.
- **Endpoints:**
  - `GET /` → student page
  - `GET /file?path=…` → read-only file content, sandboxed to `lesson_dir` + ignore list
  - `WS /ws/teacher?token=…` → receives `file`/`tree` messages (reject bad/missing token)
  - `WS /ws/student` → on connect push `tree` then current `file`; then stream updates
  - `WS /terminal` → reverse-proxy to ttyd
- **Depends on:** ttyd localhost URL, token (env), `liveclass.toml`.

### 5.3 ttyd + tmux
- tmux session (name from config) is where commands run; the teacher's VS Code terminal
  attaches to it.
- ttyd runs `tmux attach -r` — **read-only** (`-r`, never `-W`): students physically
  cannot type into the teacher's machine.
- The session is pinned to `cols × rows` from config so every student sees the same
  readable terminal regardless of the teacher's screen size.

### 5.4 student page (`static/index.html`)
Three-pane, VS Code-like:

```
┌──────────┬────────────────────────────────┐
│ EXPLORER │  main.py            ● live       │
│ ▾ lesson │  @app.get("/items/{id}")        │
│   main.py│  async def read_item(id: int):  │   code (Monaco, read-only, copyable)
│   db.py  │      return {"id": id}           │
│ ▾ models/│                                  │
│   user.py├────────────────────────────────┤
│          │  $ uvicorn main:app --reload     │   terminal (xterm.js, read-only)
└──────────┴────────────────────────────────┘
```

- **Explorer (left):** renders the tree from JSON; active file highlighted so it follows
  the teacher. Clicking any file → fetches via `/file` and shows it in the center pane,
  switching to "browsing `db.py` — [return to live]" (student-local; others unaffected).
  In live mode the center pane tracks the teacher's active file.
- **Code (center):** Monaco read-only; live updates when in live mode.
- **Terminal (bottom):** xterm.js, read-only, fed by `/terminal`.
- All panes `user-select: text`; connection-status badge; auto-reconnect on both
  sockets with backoff (re-syncs last state on reconnect).
- **Monaco and xterm.js load from a CDN, not through the tunnel.** These are the
  heaviest assets on the page; serving them from a CDN keeps them off ngrok entirely —
  a large cut to both monthly bandwidth and the 20k-request quota (Section 5.5). Only
  the small HTML shell + the live WebSocket/`/file` traffic flow through the tunnel.

### 5.5 ngrok (ingress)
Tunnel → `http://localhost:8000` using the teacher's **reserved static ngrok domain**
(so the URL is stable across sessions — the one historical ngrok downside, rotation, is
gone). Tunnels only `:8000`; ttyd and the broadcaster stay localhost-bound.

Residual risk: some campus firewalls block ngrok by domain pattern. If that bites,
`cloudflared` (a named tunnel on a custom domain) is the drop-in fallback — the
architecture is unchanged, only the tunnel binary differs.

**Free-plan limits (2026) and how the design stays under them:**
- **Data transfer: 1 GB/month** — the binding constraint. A 1-hour class costs roughly
  100–250 MB (full-file broadcasts × students + terminal stream), so free covers
  ~4–8 class-hours/month. Loading Monaco/xterm.js from a CDN (Section 5.4) keeps the
  heavy assets off the tunnel. A recurring multi-session course will eventually need
  paid ngrok or the `cloudflared` fallback (no bandwidth cap).
- **HTTP requests: 20,000/month** — WebSocket reconnects and `/file` clicks count;
  CDN-hosted assets keep this well clear for normal use.
- **Browser interstitial** — free tier injects a one-time warning page before HTML
  (cookie lasts 7 days). The `ngrok-skip-browser-warning` header does **not** apply to
  normal browser navigation, so it can't be suppressed on free — brief students to
  click through once. `cloudflared` has no interstitial.
- Concurrent endpoints (need 1), connections (~120 for 60 students), and the 4,000/min
  rate limit are all comfortably within free limits.

### 5.6 runner (`Makefile`)
`make up` starts (in order): tmux session pinned to `cols×rows`, ttyd, broadcaster,
server, cloudflared. `make down` tears them down.

## 6. Configuration — `liveclass.toml`

Lives at the **repo root, outside `./lesson/`**, so it is never part of the broadcast.

```toml
# liveclass.toml — edit any time; changes apply live, no restart needed.

[broadcast]
lesson_dir = "./lesson"
title      = "FastAPI Live"

# Glob patterns excluded from BOTH the file tree and /file fetches.
# Single source of truth for "what students don't see."
# Defaults exclude only noise/danger; dotfiles like .env are SHOWN (e2e teaching).
ignore = [
  ".git/",
  ".venv/", "venv/",
  "__pycache__/",
  "node_modules/",
  "*.pyc",
]

[terminal]
tmux_session = "class"
cols = 100      # fixed width of the streamed terminal (sized for student laptops)
rows = 30       # fixed height
```

**Hot-reload:** both broadcaster and server watch this file. On change, the broadcaster
re-walks and re-broadcasts the tree; the server re-applies the ignore list to `/file`.
Adding `secrets.py` to `ignore` and saving makes it vanish from every student's tree and
become un-fetchable mid-class, no restart.

**Both read it directly** (rather than one telling the other) so there is no window where
the server serves a file the config says to hide — enforcement never depends on a message
arriving.

**Stays out of this file:** the teacher token (env var only — config files get shared).

## 7. Protocol / message schemas

Teacher → server → students, JSON over WebSocket:

- **file:** `{ "type": "file", "path": "main.py", "language": "python", "content": "…" }`
  — full file content of the active file. `path` is relative to `lesson_dir`.
- **tree:** `{ "type": "tree", "tree": [ {"name","path","type":"file|dir","children?"} ] }`
  — full snapshot of the visible file tree (post-ignore).

`/file` response: raw file bytes as `text/plain` (sandboxed; 404 if outside `lesson_dir`
or matched by ignore).

## 8. Data flow

- **Code:** edit → auto-save → fs event → broadcaster → `/ws/teacher` → server stores +
  fans to `/ws/student` → center pane (students in live mode).
- **Tree:** add/rename/delete → broadcaster → server stores + fans → sidebars re-render.
- **Terminal:** type in tmux → ttyd → `/terminal` proxy → xterm.js. (tmux redraws the
  full screen to any new client, so late-joiners see the current terminal instantly.)
- **Student browse:** click file → `GET /file?path=` → center pane shows it (local only).
- **Late joiner:** server pushes `tree` + current `file` immediately; terminal fills on
  tmux attach.

## 9. Security

- **`GET /file` sandboxed:** resolve realpath, reject anything escaping `lesson_dir`;
  reject anything matched by the ignore list.
- **Ignore list** is the single control for visibility, enforced in both tree generation
  and `/file`. Defaults exclude `.git/`, virtualenvs, caches, `*.pyc`.
- **`.env` is visible by design (e2e teaching)** — therefore an operational rule: the
  class `.env` carries **placeholder values only**; real secrets stay outside `./lesson/`
  (shell env or a non-taught file). Visibility of a file = broadcast verbatim to all.
- **Teacher token** from env, never hardcoded, never in `liveclass.toml`.
- **Students strictly read-only on every channel:** `/ws/student` is receive-only; ttyd
  is `-r` (no `-W`).
- Only `:8000` is exposed; ttyd and broadcaster bind to localhost.

## 10. Error handling

- Bad/missing token on `/ws/teacher` → connection rejected (close with policy violation).
- Student socket drops → client auto-reconnects with backoff, re-syncs last state.
- Broadcaster can't reach server / ttyd down → retry with backoff, **logged loudly**
  (no silent swallowing).
- ttyd down → terminal pane shows "reconnecting"; server proxy retries.

## 11. Testing

- **Unit:** fan-out reaches all mock clients; late-joiner receives `tree` + current
  `file` on connect; teacher token rejection; tree generation; ignore list excludes
  `.git/` and any configured pattern; `/file` rejects path traversal and ignored paths.
- **Integration:** start server → connect student WS → write a file → assert update
  arrives; connect *after* a write → assert immediate state; add a file → assert tree
  update; edit `liveclass.toml` to add an ignore → assert file disappears and `/file`
  returns 404.
- **Manual:** two browsers on the student URL — edit, confirm live + copy; click a file
  to browse then return to live; confirm the terminal is read-only (typing does nothing).

## 12. Future upgrades (out of scope for v1)

- **VS Code extension** (~100 lines TS) replacing the file-watcher: true active-file
  follow, scroll-sync, keystroke-live (no save needed). Add only if save-debounce feels
  laggy in practice.
- **Reproducibility via Docker** if the class ever needs to run identically across
  machines or be handed to students as an image (accepts the terminal-namespace cost).
```
