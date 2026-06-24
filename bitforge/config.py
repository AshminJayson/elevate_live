"""Central configuration and the single ignore-matching rule for BitForge.

All configuration lives in one place: a pydantic-settings `Settings` model
sourced from the process environment and an optional project-root `.env`
file. Every key shares the `BITFORGE_` prefix, so a single `.env` is the one
configuration endpoint. Exported environment variables take precedence over
`.env` (pydantic-settings' default source priority), so CI/overrides keep
working.

This module also owns `is_ignored`, the single visibility rule used by both
the file-tree walk and the /file endpoint.
"""

from fnmatch import fnmatch
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_IGNORE = [
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "node_modules/",
    "*.pyc",
]


class Settings(BaseSettings):
    """Resolved BitForge configuration, loaded from env / .env.

    Sources, highest priority first: constructor kwargs, environment variables
    (e.g. BITFORGE_TOKEN), the project-root `.env` file, then field defaults.

    Fields (env key is the field name upper-cased with the BITFORGE_ prefix):
        token (str): host auth token; "" means no host may connect.
            Env: BITFORGE_TOKEN.
        cloudflared_token (str): credential for the cloudflared *named* tunnel,
            copied from the Cloudflare Zero Trust dashboard. Passed verbatim to
            `cloudflared tunnel run --token <value>`; it encodes which tunnel to
            run and its dashboard-configured ingress (public hostname ->
            localhost:8000). REQUIRED -- the tunnel cannot start without it.
            Env: BITFORGE_CLOUDFLARED_TOKEN.
        public_url (str): the public hostname mapped to the named tunnel in the
            Cloudflare dashboard (e.g. "https://class.example.com"). cloudflared
            never prints this for a named tunnel, so it is configured here only
            to echo a "share this" line on startup; "" suppresses that line.
            Env: BITFORGE_PUBLIC_URL.
        source_dir (Path): absolute directory broadcast to viewers (resolved
            from whatever is given). Env: BITFORGE_SOURCE_DIR.
        title (str): page title. Env: BITFORGE_TITLE.
        ignore (list[str]): glob/dir patterns hidden from the tree and /file;
            given in .env as a JSON array. Env: BITFORGE_IGNORE.
        tmux_session (str): shared tmux session name. Env: BITFORGE_TMUX_SESSION.
        cols (int): fixed terminal width. Env: BITFORGE_COLS.
        rows (int): fixed terminal height. Env: BITFORGE_ROWS.
        log_file (str): path (relative to the launch cwd, or absolute) for the
            detailed append-mode log; the console stays quiet. Env:
            BITFORGE_LOG_FILE.
        heartbeat_seconds (int): interval between console heartbeat lines
            reporting viewers online. Env: BITFORGE_HEARTBEAT_SECONDS.
    """

    model_config = SettingsConfigDict(
        env_prefix="BITFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    token: str = ""
    cloudflared_token: str = ""
    public_url: str = ""
    source_dir: Path = Path("./source")
    title: str = "BitForge"
    ignore: list[str] = DEFAULT_IGNORE
    tmux_session: str = "class"
    cols: int = 100
    rows: int = 30
    log_file: str = "bitforge.log"
    heartbeat_seconds: int = 10

    @field_validator("source_dir")
    @classmethod
    def _resolve_source_dir(cls, value: Path) -> Path:
        """Resolve source_dir to an absolute path so the sandbox root is stable."""
        return Path(value).resolve()


def load_settings() -> Settings:
    """Load configuration from the environment and `.env`.

    Algorithm:
        Construct a fresh `Settings`, which reads (in priority order) any set
        BITFORGE_* environment variables, then `.env` in the current working
        directory, then field defaults. A fresh instance is returned each call
        so callers that re-read it pick up live `.env` edits (hot reload).

    Returns:
        Settings: the resolved configuration.
    """
    return Settings()


def is_ignored(rel_path: str, ignore_patterns: list[str]) -> bool:
    """Return True if a POSIX-relative path matches any ignore pattern.

    Algorithm:
        For each pattern: if it ends with "/", treat its stem as a directory
        name and match if that name appears as any path segment. Otherwise
        treat it as a glob and match against the full path or the basename.

    Args:
        rel_path (str): POSIX-relative path (e.g. "pkg/main.py").
        ignore_patterns (list[str]): patterns from config.

    Returns:
        bool: True if the path should be hidden.
    """
    parts = rel_path.split("/")
    for pattern in ignore_patterns:
        if pattern.endswith("/"):
            if pattern.rstrip("/") in parts:
                return True
        elif fnmatch(rel_path, pattern) or fnmatch(parts[-1], pattern):
            return True
    return False


if __name__ == "__main__":
    settings = load_settings()
    shown = settings.model_dump()
    shown["token"] = "***" if settings.token else ""  # never print the secret
    shown["cloudflared_token"] = "***" if settings.cloudflared_token else ""
    print(shown)
