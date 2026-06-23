import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

/**
 * Resolved extension configuration.
 *
 * Schema:
 *   serverUrl  (string): WebSocket base, e.g. "ws://127.0.0.1:8000".
 *   token      (string): BITFORGE_TOKEN read from the workspace-root .env.
 *   lessonDir  (string): absolute lesson directory root.
 *   debounceMs (number): debounce window for buffer sends.
 *   ignore     (string[]): patterns whose files are never streamed.
 */
export interface BitForgeConfig {
  serverUrl: string;
  token: string;
  lessonDir: string;
  debounceMs: number;
  ignore: string[];
}

/**
 * Build configuration from VS Code settings plus the workspace-root .env token.
 *
 * Algorithm: read the "bitforge.*" settings; resolve lessonDir (blank -> first
 * workspace folder); read the token from "<workspaceRoot>/.env" (BITFORGE_TOKEN).
 * The token lives in .env (git-ignored) rather than settings so it is never
 * committed. Returns null if there is no workspace folder or no token, after
 * showing the user an actionable error.
 *
 * @returns the resolved config, or null when it cannot be assembled
 */
export function loadConfig(): BitForgeConfig | null {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showErrorMessage("BitForge: open a workspace folder to stream a lesson.");
    return null;
  }
  const root = folders[0].uri.fsPath;
  const cfg = vscode.workspace.getConfiguration("bitforge");
  const env = readEnv(path.join(root, ".env"));

  const token = env["BITFORGE_TOKEN"] || "";
  if (!token) {
    vscode.window.showErrorMessage(
      "BitForge: set BITFORGE_TOKEN in the workspace-root .env to start streaming."
    );
    return null;
  }

  // lessonDir precedence: the bitforge.lessonDir setting, else BITFORGE_LESSON_DIR
  // from the same .env (resolved relative to the .env's folder, matching the
  // server's single-config model), else the workspace root.
  const lessonDirSetting = (cfg.get<string>("lessonDir") || "").trim();
  const lessonEnv = (env["BITFORGE_LESSON_DIR"] || "").trim();
  let lessonDir: string;
  if (lessonDirSetting) {
    lessonDir = path.resolve(lessonDirSetting);
  } else if (lessonEnv) {
    lessonDir = path.resolve(root, lessonEnv);
  } else {
    lessonDir = root;
  }

  return {
    serverUrl: (cfg.get<string>("serverUrl") || "ws://127.0.0.1:8000").replace(/\/+$/, ""),
    token,
    lessonDir,
    debounceMs: cfg.get<number>("debounceMs") ?? 100,
    ignore: cfg.get<string[]>("ignore") ?? [],
  };
}

/**
 * Parse a .env file into a key->value map, or {} if unreadable.
 *
 * Reads the same file the BitForge server reads. Handles simple KEY=VALUE lines,
 * skipping blanks and "#" comments and stripping optional surrounding quotes.
 * Deliberately minimal (no interpolation/multiline); enough for the few keys the
 * extension needs (BITFORGE_TOKEN, BITFORGE_LESSON_DIR).
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
