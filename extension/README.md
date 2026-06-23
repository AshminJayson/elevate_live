# BitForge Live Sync (VS Code extension)

Streams the **active editor's unsaved buffer** to the BitForge hub on every change,
so students follow your typing live in the code pane — no save required. (The
filesystem broadcaster still owns the file tree and saved-file fallback; this
extension just adds the live active-file content.)

## How it works

On each edit (debounced ~100ms) and on switching the active editor, the extension
sends `{type:"file", path, content}` over a persistent `/ws/teacher` WebSocket —
the same channel and protocol the broadcaster uses. The server validates the path
(sandbox + ignore) and derives the language, so the extension stays thin.

## Configure

The teacher **token** is entered via a prompt the first time you stream and kept
in VS Code **SecretStorage** (OS keychain) — never in a file, never synced, and
not tied to any project. Commands: **BitForge: Set token** / **Clear token**. If
the hub rejects it, you'll be asked to enter it again. (No `.env` is required in
the project you open.)

Everything else is VS Code settings (per-workspace in `.vscode/settings.json`):

| Setting | Default | Purpose |
|---|---|---|
| `bitforge.serverUrl` | `ws://127.0.0.1:8000` | Hub base; connects to `<serverUrl>/ws/teacher`. |
| `bitforge.lessonDir` | _(workspace folder, or `BITFORGE_LESSON_DIR` from a workspace `.env`)_ | Lesson root; only files under it stream. |
| `bitforge.debounceMs` | `100` | Debounce window after the last keystroke. |
| `bitforge.ignore` | see settings | Patterns never streamed (mirrors the server). |

## Develop / run

```
cd extension
npm install
npm run watch      # compile in the background
```

Press **F5** in VS Code to launch an Extension Development Host. In that host, open
your lesson workspace (with `.env` containing `BITFORGE_TOKEN`) while the BitForge
server is running (`make up`). The status bar shows igniting → forging.

## Package

```
npm run package                       # produces bitforge-live-sync-<version>.vsix
code --install-extension bitforge-live-sync-*.vsix
```
