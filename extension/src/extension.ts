import * as vscode from "vscode";
import { BitForgeConfig, loadConfig } from "./config";
import { ConnState, TeacherConnection } from "./connection";
import { isIgnored, toLessonRelative } from "./paths";

let connection: TeacherConnection | null = null;
let status: vscode.StatusBarItem;
let debounceTimer: NodeJS.Timeout | null = null;
let config: BitForgeConfig | null = null;

/**
 * Activate the extension: load config, connect, and stream the active buffer.
 *
 * Registers listeners for document edits and active-editor changes; both feed a
 * debounced send of the active editor's UNSAVED buffer (so students follow the
 * teacher's typing without a save). Streams only files under lessonDir that are
 * not ignored. The server re-derives language and re-checks the sandbox/ignore,
 * so the wire message is just {type:"file", path, content}.
 *
 * @param context the extension context (subscriptions are disposed on shutdown)
 */
export function activate(context: vscode.ExtensionContext): void {
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "bitforge.toggleStreaming";
  context.subscriptions.push(status);

  start();

  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.document === vscode.window.activeTextEditor?.document) {
        scheduleStream();
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => scheduleStream()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("bitforge")) {
        restart();
      }
    }),
    vscode.commands.registerCommand("bitforge.toggleStreaming", toggle),
    vscode.commands.registerCommand("bitforge.reconnect", restart)
  );
}

/** Tear down the connection and timers on deactivate. */
export function deactivate(): void {
  stop();
}

/** Load config and open the teacher connection; no-op if config is incomplete. */
function start(): void {
  config = loadConfig();
  if (!config) {
    setStatus("stopped");
    return;
  }
  connection = new TeacherConnection(
    `${config.serverUrl}/ws/teacher?token=${encodeURIComponent(config.token)}`,
    setStatus
  );
  connection.connect();
  scheduleStream(); // push the current file immediately on (re)start
}

/** Close the connection and clear the debounce timer. */
function stop(): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  if (connection) {
    connection.dispose();
    connection = null;
  }
}

/** Stop then start, e.g. after a settings change or a manual reconnect. */
function restart(): void {
  stop();
  start();
}

/** Toggle streaming on/off from the status bar or command palette. */
function toggle(): void {
  if (connection) {
    stop();
    setStatus("stopped");
  } else {
    start();
  }
}

/** Debounce a buffer send by config.debounceMs (trailing edge). */
function scheduleStream(): void {
  if (!config || !connection) {
    return;
  }
  if (debounceTimer) {
    clearTimeout(debounceTimer);
  }
  debounceTimer = setTimeout(streamActive, config.debounceMs);
}

/**
 * Send the active editor's current buffer as a 'file' message, if eligible.
 *
 * Bails when there is no active editor, the file is outside lessonDir, or the
 * path is ignored. Sends the full buffer text every time (idempotent), so a
 * dropped send is recovered by the next change.
 */
function streamActive(): void {
  if (!config || !connection) {
    return;
  }
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }
  const rel = toLessonRelative(editor.document.uri.fsPath, config.lessonDir);
  if (rel === null || isIgnored(rel, config.ignore)) {
    return;
  }
  connection.send({ type: "file", path: rel, content: editor.document.getText() });
}

/** Reflect connection state in the status bar with a forge-themed label. */
function setStatus(state: ConnState): void {
  const labels: Record<ConnState, string> = {
    connecting: "$(sync~spin) BitForge: igniting",
    live: "$(flame) BitForge: forging",
    reconnecting: "$(sync~spin) BitForge: cooling",
    stopped: "$(circle-slash) BitForge: off",
  };
  status.text = labels[state];
  status.tooltip = "BitForge live sync — click to toggle";
  status.show();
}
