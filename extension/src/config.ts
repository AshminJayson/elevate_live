import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

/**
 * Resolved non-secret extension configuration.
 *
 * The token is deliberately NOT here: it is entered via a prompt and kept in
 * VS Code SecretStorage (see extension.ts), so it never lives in a file and the
 * extension works in any opened project without a .env.
 *
 * Schema:
 *   serverUrl  (string): WebSocket base, e.g. "ws://127.0.0.1:8000".
 *   sourceDir  (string): absolute source directory root.
 *   debounceMs (number): debounce window for buffer sends.
 *   ignore     (string[]): patterns whose files are never streamed.
 */
export interface BitForgeConfig {
  serverUrl: string;
  sourceDir: string;
  debounceMs: number;
  ignore: string[];
}

/**
 * Build non-secret configuration from VS Code settings (+ .env for sourceDir).
 *
 * Algorithm: require a workspace folder; resolve sourceDir with precedence
 * setting > BITFORGE_SOURCE_DIR in the workspace .env > the workspace root (the
 * .env read is a soft convenience, never required); read serverUrl/debounce/
 * ignore from settings. Returns null only when there is no workspace folder.
 *
 * @returns the resolved config, or null when no folder is open
 */
export function loadConfig(): BitForgeConfig | null {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showErrorMessage("BitForge: open a workspace folder to stream a source.");
    return null;
  }
  const root = folders[0].uri.fsPath;
  const cfg = vscode.workspace.getConfiguration("bitforge");
  const env = readEnv(path.join(root, ".env"));

  // sourceDir precedence: the bitforge.sourceDir setting, else BITFORGE_SOURCE_DIR
  // from the workspace .env (resolved relative to it, matching the server's
  // single-config model), else the workspace root.
  const sourceDirSetting = (cfg.get<string>("sourceDir") || "").trim();
  const sourceEnv = (env["BITFORGE_SOURCE_DIR"] || "").trim();
  let sourceDir: string;
  if (sourceDirSetting) {
    sourceDir = path.resolve(sourceDirSetting);
  } else if (sourceEnv) {
    sourceDir = path.resolve(root, sourceEnv);
  } else {
    sourceDir = root;
  }

  return {
    serverUrl: (cfg.get<string>("serverUrl") || "ws://127.0.0.1:8000").replace(/\/+$/, ""),
    sourceDir,
    debounceMs: cfg.get<number>("debounceMs") ?? 100,
    ignore: cfg.get<string[]>("ignore") ?? [],
  };
}

/**
 * Parse a .env file into a key->value map, or {} if unreadable.
 *
 * Used only for the non-secret BITFORGE_SOURCE_DIR convenience. Handles simple
 * KEY=VALUE lines, skipping blanks and "#" comments and stripping optional
 * surrounding quotes.
 *
 * @param envPath absolute path to the .env file
 * @returns map of env keys to string values (empty when the file is absent)
 */
function readEnv(envPath: string): Record<string, string> {
  const out: Record<string, string> = {};
  let text: string;
  try {
    text = fs.readFileSync(envPath, "utf-8");
  } catch {
    return out;
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const eq = line.indexOf("=");
    if (eq === -1) {
      continue;
    }
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
  }
  return out;
}
