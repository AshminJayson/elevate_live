from pathlib import Path

from liveclass.config import Config, DEFAULT_IGNORE, is_ignored, load_config


def test_load_config_reads_values(tmp_path):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text(
        '[broadcast]\n'
        'lesson_dir = "./lesson"\n'
        'title = "FastAPI Live"\n'
        'ignore = [".git/", "*.pyc"]\n'
        '[terminal]\n'
        'tmux_session = "class"\n'
        'cols = 100\n'
        'rows = 30\n'
    )
    cfg = load_config(cfg_file, token="secret")
    assert isinstance(cfg, Config)
    assert cfg.title == "FastAPI Live"
    assert cfg.ignore == [".git/", "*.pyc"]
    assert cfg.tmux_session == "class"
    assert cfg.cols == 100
    assert cfg.rows == 30
    assert cfg.token == "secret"
    assert cfg.lesson_dir.is_absolute()


def test_load_config_token_from_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text('[broadcast]\nlesson_dir = "./lesson"\n')
    monkeypatch.setenv("LIVECLASS_TOKEN", "from-env")
    cfg = load_config(cfg_file)
    assert cfg.token == "from-env"


def test_load_config_defaults(tmp_path):
    cfg_file = tmp_path / "liveclass.toml"
    cfg_file.write_text("")
    cfg = load_config(cfg_file, token="t")
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
