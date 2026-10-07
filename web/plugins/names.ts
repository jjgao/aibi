/**
 * The names a build gives its files (D411): `[name]-[hash]` with Vite's 8-character hash, under
 * the loader's grammar for a file of `assets/` (`^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$`, no `..`).
 */

/** A file name's part a module's name gives, made one the loader admits: every character but
 * ASCII letters, digits, `.`, `_` and `-` becomes `_`, and so does a leading `.` or `-`.
 * Rolldown's default keeps a space, `@`, `~`, `'`, `(`, non-ASCII letters and a leading `-` or
 * `.`, each of which the loader refuses. */
export function sanitizeFileName(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]/gu, "_").replace(/^[.-]/u, "_");
}

export const ENTRY_FILE_NAMES = "assets/[name]-[hash].js";
export const CHUNK_FILE_NAMES = "assets/[name]-[hash].js";
export const ASSET_FILE_NAMES = "assets/[name]-[hash][extname]";
