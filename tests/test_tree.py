from bitforge.tree import build_tree


def _make_source(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')")
    (tmp_path / ".env").write_text("API_KEY=changeme")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "user.py").write_text("class User: ...")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.pyc").write_text("x")
    return tmp_path


def test_build_tree_structure(tmp_path):
    _make_source(tmp_path)
    tree = build_tree(tmp_path, [])
    names = [n["name"] for n in tree]
    # directories first, then files, each alphabetical
    assert names.index("models") < names.index(".env")
    models = next(n for n in tree if n["name"] == "models")
    assert models["type"] == "dir"
    assert models["children"][0]["path"] == "models/user.py"


def test_build_tree_respects_ignore(tmp_path):
    _make_source(tmp_path)
    tree = build_tree(tmp_path, ["__pycache__/"])
    assert all(n["name"] != "__pycache__" for n in tree)


def test_build_tree_shows_dotfiles(tmp_path):
    _make_source(tmp_path)
    tree = build_tree(tmp_path, ["__pycache__/"])
    assert any(n["name"] == ".env" for n in tree)
