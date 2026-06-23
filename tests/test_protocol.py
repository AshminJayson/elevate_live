from bitforge.protocol import detect_language, file_message, tree_message


def test_detect_language_known():
    assert detect_language("main.py") == "python"
    assert detect_language("a/b/app.ts") == "typescript"
    assert detect_language("data.json") == "json"


def test_detect_language_unknown():
    assert detect_language("Dockerfile") == "plaintext"
    assert detect_language(".env") == "plaintext"


def test_file_message():
    msg = file_message("main.py", "print(1)")
    assert msg == {
        "type": "file",
        "path": "main.py",
        "language": "python",
        "content": "print(1)",
    }


def test_tree_message():
    tree = [{"name": "main.py", "path": "main.py", "type": "file"}]
    assert tree_message(tree) == {"type": "tree", "tree": tree}
