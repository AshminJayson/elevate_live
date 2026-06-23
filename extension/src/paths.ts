import * as path from "path";

/**
 * Return the POSIX-relative path of a file under sourceDir, or null if outside it.
 *
 * Algorithm: compute the OS-relative path from sourceDir to filePath; if it starts
 * with ".." or is absolute, the file escapes the source sandbox, so return null.
 * Otherwise normalise separators to POSIX (the wire protocol and server use "/").
 *
 * @param filePath absolute filesystem path of the document
 * @param sourceDir absolute source directory root
 * @returns POSIX-relative path (e.g. "pkg/main.py") or null when outside sourceDir
 */
export function toSourceRelative(filePath: string, sourceDir: string): string | null {
  const rel = path.relative(sourceDir, filePath);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    return null;
  }
  return rel.split(path.sep).join("/");
}

/**
 * Return true if a POSIX-relative path matches any ignore pattern.
 *
 * Faithful port of the server's bitforge.config.is_ignored so client and server
 * agree. A pattern ending in "/" matches if its stem appears as any path segment
 * (a directory). Otherwise it is a glob matched against the full path or basename.
 *
 * @param relPath POSIX-relative path (e.g. "pkg/main.py")
 * @param patterns ignore patterns (e.g. ["__pycache__/", "*.pyc"])
 * @returns true if the path should be hidden / not streamed
 */
export function isIgnored(relPath: string, patterns: string[]): boolean {
  const parts = relPath.split("/");
  const base = parts[parts.length - 1];
  for (const pattern of patterns) {
    if (pattern.endsWith("/")) {
      if (parts.includes(pattern.slice(0, -1))) {
        return true;
      }
    } else if (globMatch(relPath, pattern) || globMatch(base, pattern)) {
      return true;
    }
  }
  return false;
}

/**
 * Minimal fnmatch-style glob: supports "*", "?", and character classes "[...]".
 * Mirrors Python's fnmatch.fnmatch semantics closely enough for ignore patterns.
 *
 * @param name string to test
 * @param pattern glob pattern
 * @returns true if name matches pattern
 */
function globMatch(name: string, pattern: string): boolean {
  let re = "";
  for (const ch of pattern) {
    if (ch === "*") {
      re += ".*";
    } else if (ch === "?") {
      re += ".";
    } else if ("\\^$.|+()[]{}".includes(ch)) {
      re += "\\" + ch;
    } else {
      re += ch;
    }
  }
  return new RegExp("^" + re + "$").test(name);
}
