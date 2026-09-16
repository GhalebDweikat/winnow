from winnow.extract import extract, trimmed_input


def test_bash_extracts_stdout_and_preserves_other_fields():
    resp = {"stdout": "a\nb", "stderr": "warn", "interrupted": False, "isImage": False}
    ex = extract("Bash", {"command": "ls -la"}, resp)
    assert ex is not None and ex.text == "a\nb" and ex.line_offset == 1
    rebuilt = ex.rebuild("only a")
    assert rebuilt == {"stdout": "only a", "stderr": "warn", "interrupted": False, "isImage": False}
    assert resp["stdout"] == "a\nb"  # original untouched


def test_read_extracts_file_content_and_updates_numlines():
    resp = {"type": "text", "file": {"filePath": "C:\\x.py", "content": "1\n2\n3", "numLines": 3, "startLine": 40, "totalLines": 300}}
    ex = extract("Read", {"file_path": "C:\\x.py"}, resp)
    assert ex is not None and ex.line_offset == 40
    rebuilt = ex.rebuild("1\n[stub]")
    assert rebuilt["file"]["content"] == "1\n[stub]"
    assert rebuilt["file"]["numLines"] == 2
    assert rebuilt["file"]["totalLines"] == 300
    assert resp["file"]["content"] == "1\n2\n3"


def test_grep_content_mode_only():
    assert extract("Grep", {"pattern": "x"}, {"mode": "files_with_matches", "filenames": ["a"], "content": ""}) is None
    ex = extract("Grep", {"pattern": "x", "path": "src"}, {"mode": "content", "content": "a:1:x\nb:2:x", "numLines": 2})
    assert ex is not None and "pattern=x" in ex.describe
    assert ex.rebuild("a:1:x")["numLines"] == 1


def test_string_response_passes_through_as_string():
    ex = extract("mcp__x__y", {"q": "hi"}, "plain text result")
    assert ex is not None and ex.rebuild("new") == "new"


def test_unknown_shapes_return_none():
    assert extract("Read", {}, "File not found") is not None  # strings are still judgeable
    assert extract("Bash", {}, {"nope": 1}) is None
    assert extract("Glob", {}, {"filenames": ["a", "b"]}) is None


def test_trimmed_input_is_compact():
    assert trimmed_input("Bash", {"command": "x" * 1000})["command"].endswith("\u2026")
    assert trimmed_input("Read", {"file_path": "f", "offset": 1, "junk": 2}) == {"file_path": "f", "offset": 1}
