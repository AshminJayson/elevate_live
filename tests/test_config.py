from liveclass.config import DEFAULT_IGNORE, Settings, is_ignored


def _write_env(tmp_path, body):
    """Write a .env file and return its path."""
    env_file = tmp_path / ".env"
    env_file.write_text(body)
    return env_file


def test_settings_reads_env_file(tmp_path):
    env_file = _write_env(
        tmp_path,
        'LIVECLASS_TOKEN=secret\n'
        'LIVECLASS_LESSON_DIR=./lesson\n'
        'LIVECLASS_TITLE=FastAPI Live\n'
        'LIVECLASS_IGNORE=[".git/", "*.pyc"]\n'
        'LIVECLASS_TMUX_SESSION=class\n'
        'LIVECLASS_COLS=100\n'
        'LIVECLASS_ROWS=30\n',
    )
    cfg = Settings(_env_file=env_file)
    assert cfg.token == "secret"
    assert cfg.title == "FastAPI Live"
    assert cfg.ignore == [".git/", "*.pyc"]
    assert cfg.tmux_session == "class"
    assert cfg.cols == 100
    assert cfg.rows == 30
    assert cfg.lesson_dir.is_absolute()


def test_settings_env_overrides_dotenv(tmp_path, monkeypatch):
    env_file = _write_env(tmp_path, "LIVECLASS_TOKEN=from-file\n")
    monkeypatch.setenv("LIVECLASS_TOKEN", "from-env")
    cfg = Settings(_env_file=env_file)
    assert cfg.token == "from-env"  # exported env wins over .env


def test_settings_defaults(monkeypatch):
    for key in ("LIVECLASS_TOKEN", "LIVECLASS_IGNORE", "LIVECLASS_COLS",
                "LIVECLASS_ROWS", "LIVECLASS_TMUX_SESSION"):
        monkeypatch.delenv(key, raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.token == ""
    assert cfg.ignore == DEFAULT_IGNORE
    assert cfg.cols == 100
    assert cfg.rows == 30
    assert cfg.tmux_session == "class"


def test_is_ignored_directory_pattern():
    assert is_ignored(".git/config", [".git/"]) is True
    assert is_ignored("pkg/__pycache__/x.pyc", ["__pycache__/"]) is True


def test_is_ignored_glob_pattern():
    assert is_ignored("main.pyc", ["*.pyc"]) is True
    assert is_ignored("sub/main.pyc", ["*.pyc"]) is True


def test_dotfiles_are_shown_by_default():
    assert is_ignored(".env", DEFAULT_IGNORE) is False
    assert is_ignored(".gitignore", DEFAULT_IGNORE) is False


def test_is_ignored_negative():
    assert is_ignored("main.py", DEFAULT_IGNORE) is False
