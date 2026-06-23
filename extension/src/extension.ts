import * as vscode from "vscode";
import { BitForgeConfig, loadConfig } from "./config";
import { ConnState, TeacherConnection } from "./connection";
import { isIgnored, toLessonRelative } from "./paths";

const TOKEN_KEY = "bitforge.token";

let context: vscode.ExtensionContext;
let connection: TeacherConnection | null = null;
let status: vscode.StatusBarItem;
let debounceTimer: NodeJS.Timeout | null = null;
let config: BitForgeConfig | null = null;

/**
 * Activate the extension: wire the status bar, listeners, and commands.
 *
 * Activation does NOT connect or prompt — streaming is opt-in. The status bar
 * starts in the "off" state; the teacher starts streaming by clicking it or
 * running the toggle command, at which point the token is prompted for (if
 * missing) and stored in VS Code SecretStorage (OS keychain), not a .env file —
 * so the extension works in any opened project with no per-project secret file.
 * Once streaming, edits and active-editor changes feed a debounced send of the
 * UNSAVED buffer; the server re-derives language and re-checks the
 * sandbox/ignore, so the wire message is just {type,path,content}.
 *
 * @param ctx the extension context (provides SecretStorage; subscriptions are
 *   disposed on shutdown)
 */
export function activate(ctx: vscode.ExtensionContext): void {
  context = ctx;
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "bitforge.toggleStreaming";
  ctx.subscriptions.push(status);

  // Do not connect or prompt for a token on startup — streaming is opt-in. The
  // status bar shows "off"; the teacher clicks it (or runs the toggle command)
  // to start, which is when getToken() prompts for the token if it is missing.
  setStatus("stopped");

  ctx.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.document === vscode.window.activeTextEditor?.document) {
        scheduleStream();
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => scheduleStream()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("bitforge")) {
        void restart();
      }
    }),
    vscode.commands.registerCommand("bitforge.toggleStreaming", toggle),
    vscode.commands.registerCommand("bitforge.reconnect", () => void restart()),
    vscode.commands.registerCommand("bitforge.setToken", setToken),
    vscode.commands.registerCommand("bitforge.clearToken", clearToken)
  );
}

/** Tear down the connection and timers on deactivate. */
export function deactivate(): void {
  stop();
}

/**
 * Get the teacher token from SecretStorage, prompting once if it is missing.
 *
 * @param promptIfMissing when true, show an input box and store what is entered
 * @returns the token, or "" if unset and not entered
 */
async function getToken(promptIfMissing: boolean): Promise<string> {
  let token = await context.secrets.get(TOKEN_KEY);
  if (!token && promptIfMissing) {
    token = await promptForToken();
  }
  return token || "";
}

/** Prompt for the token (masked) and store it in SecretStorage; returns it. */
async function promptForToken(): Promise<string> {
  const value = await vscode.window.showInputBox({
    title: "BitForge teacher token",
    prompt: "Enter the teacher token (matches the hub's BITFORGE_TOKEN). Stored securely in VS Code.",
    password: true,
    ignoreFocusOut: true,
  });
  if (value) {
    await context.secrets.store(TOKEN_KEY, value);
  }
  return value || "";
}

/** Load config + token and open the teacher connection. */
async function start(): Promise<void> {
  config = loadConfig();
  if (!config) {
    setStatus("stopped");
    return;
  }
  const token = await getToken(true);
  if (!token) {
    setStatus("stopped");
    vscode.window.showWarningMessage("BitForge: no token set — streaming is off. Run 'BitForge: Set token'.");
    return;
  }
  connection = new TeacherConnection(
    `${config.serverUrl}/ws/teacher?token=${encodeURIComponent(token)}`,
    setStatus,
    onAuthRejected
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

/** Stop then start, e.g. after a settings change, token change, or reconnect. */
async function restart(): Promise<void> {
  stop();
  await start();
}

/** Toggle streaming on/off from the status bar or command palette. */
function toggle(): void {
  if (connection) {
    stop();
    setStatus("stopped");
  } else {
    void start();
  }
}

/** Command: (re)enter the token, then reconnect with it. */
async function setToken(): Promise<void> {
  const token = await promptForToken();
  if (token) {
    await restart();
  }
}

/** Command: forget the stored token and stop streaming. */
async function clearToken(): Promise<void> {
  await context.secrets.delete(TOKEN_KEY);
  stop();
  setStatus("stopped");
  vscode.window.showInformationMessage("BitForge: token cleared.");
}

/** Hub rejected the token (1008): forget it and offer to enter a new one. */
async function onAuthRejected(): Promise<void> {
  await context.secrets.delete(TOKEN_KEY);
  setStatus("stopped");
  const pick = await vscode.window.showErrorMessage(
    "BitForge: the hub rejected the token.",
    "Enter token"
  );
  if (pick === "Enter token") {
    await restart();
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

/** Reflect connection state in the status bar. */
function setStatus(state: ConnState): void {
  const labels: Record<ConnState, string> = {
    connecting: "$(sync~spin) BitForge: connecting",
    live: "$(flame) BitForge: streaming",
    reconnecting: "$(sync~spin) BitForge: reconnecting",
    stopped: "$(circle-slash) BitForge: off",
  };
  status.text = labels[state];
  status.tooltip = "BitForge live sync — click to toggle";
  status.show();
}
