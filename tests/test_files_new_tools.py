"""PR additions integrated with the versioned editing and bounded search contract."""
from __future__ import annotations

import asyncio
import ast
import base64
from pathlib import Path

import pytest

from macos_local_mcp.files import Files
from macos_local_mcp.guard import Guard, PROJECT
from macos_local_mcp.search import search_text
from macos_local_mcp.server import build_server, INSTRUCTIONS


@pytest.fixture
def files(tmp_path):
    guard = Guard(tmp_path / "state")
    return Files(guard), guard, tmp_path


def test_edit_text_unique_match_replaces_once(files):
    f, _guard, tmp = files
    p = tmp / "code.py"
    original = "def a():\r\n    return 1\r\n\r\ndef b():\n    return 2\n"
    f.write(str(p), original)
    version = f.info(str(p))["version"]
    result = f.edit_text(str(p), "return 1", "return 42", version)
    assert result["replacements"] == 1
    assert p.read_bytes() == original.replace("return 1", "return 42").encode()
    assert Path(result["backup_path"]).read_bytes() == original.encode()


def test_edit_text_requires_unique_match_and_version(files):
    f, _guard, tmp = files
    p = tmp / "dup.txt"
    f.write(str(p), "x = 1\ny = 1\n")
    version = f.info(str(p))["version"]
    with pytest.raises(ValueError, match="exactly once"):
        f.edit_text(str(p), "1", "2", version)
    with pytest.raises(ValueError, match="expected_version"):
        f.edit_text(str(p), "x = 1", "x = 2", "")
    assert p.read_bytes() == b"x = 1\ny = 1\n"


def test_edit_text_conflict_detection_and_missing_needle(files):
    f, _guard, tmp = files
    p = tmp / "c.txt"
    f.write(str(p), "hello")
    version = f.info(str(p))["version"]
    with pytest.raises(ValueError, match="not found"):
        f.edit_text(str(p), "zzz", "q", version)
    assert f.edit_text(str(p), "hello", "hello", version)["changed"] is False
    f.write(str(p), "hello world", overwrite=True)
    with pytest.raises(ValueError, match="changed since"):
        f.edit_text(str(p), "hello", "q", version)


def test_edit_text_rejects_undecodable_bytes(files):
    f, _guard, tmp = files
    p = tmp / "bad.bin"
    f.write(str(p), base64.b64encode(b"\xff\xfe\x00utf-8").decode(), encoding="base64")
    with pytest.raises(UnicodeDecodeError):
        f.edit_text(str(p), "x", "y", f.info(str(p))["version"])


def test_search_text_tree_keeps_main_literal_contract(files):
    f, _guard, tmp = files
    f.write(str(tmp / "a.py"), "def hello():\n    pass\n")
    f.mkdir(str(tmp / "sub"))
    f.write(str(tmp / "sub" / "b.py"), "hello world\n")
    hits = search_text(f, str(tmp), "hello")["results"]
    assert {Path(h["path"]).name for h in hits} == {"a.py", "b.py"}
    assert search_text(f, str(tmp), r"def \w+")["results"] == []
    assert search_text(f, str(tmp), "absent-needle")["results"] == []


def test_search_text_never_descends_into_guard_state(files):
    f, guard, tmp = files
    p = tmp / "leaf.txt"
    f.write(str(p), "marker-in-file\n")
    f.write(str(p), "marker-in-file\n", overwrite=True)
    hits = search_text(f, str(tmp), "marker-in-file")["results"]
    assert all(guard.state not in Path(h["path"]).parents for h in hits)
    assert len(hits) == 1
    assert all(guard.state not in Path(h["path"]).parents
               for h in f.glob_files(str(tmp), "*")["matches"])


def test_glob_files_matches_and_skips(files):
    f, _guard, tmp = files
    f.mkdir(str(tmp / "node_modules"))
    f.write(str(tmp / "keep.py"), "x")
    f.write(str(tmp / "node_modules" / "junk.py"), "x")
    result = f.glob_files(str(tmp), "*.py")
    assert [Path(m["path"]).name for m in result["matches"]] == ["keep.py"]
    assert result["truncated"] is False
    with pytest.raises(ValueError):
        f.glob_files(str(tmp), "")


def test_glob_no_matches_still_obeys_entry_cap(files):
    f, _guard, tmp = files
    tree = tmp / "tree"
    tree.mkdir()
    for i in range(4):
        (tree / f"{i}.txt").write_text("x")
    result = f.glob_files(str(tree), "*.py", max_entries=2)
    assert result["matches"] == []
    assert result["entries_scanned"] == 2
    assert result["truncated"] and result["stop_reason"] == "entry_limit"


def test_file_hash_changes_with_content(files):
    f, _guard, tmp = files
    p = tmp / "h.txt"
    f.write(str(p), "one")
    first = f.file_hash(str(p))
    f.write(str(p), "two", overwrite=True)
    second = f.file_hash(str(p), algorithm="md5")
    assert first["hex"] != second["hex"]
    assert len(first["hex"]) == 64 and len(second["hex"]) == 32
    assert first["bytes"] == second["bytes"] == 3
    with pytest.raises(ValueError):
        f.file_hash(str(p), algorithm="crc32")


def test_file_hash_rejects_concurrent_changes(files, monkeypatch):
    import macos_local_mcp.files as module
    f, guard, tmp = files
    p = tmp / "changing.txt"
    p.write_bytes(b"abcdef")
    monkeypatch.setattr(module, "MAX_READ", 2)
    original_check = guard.check
    checks = 0
    def mutate():
        nonlocal checks
        original_check()
        checks += 1
        if checks == 3:
            p.write_bytes(b"different-length")
    monkeypatch.setattr(guard, "check", mutate)
    with pytest.raises(ValueError, match="changed"):
        f.file_hash(str(p))


def test_protected_write_prefixes_are_rejected(files):
    f, _guard, _tmp = files
    for target in ("~/.ssh/authorized_keys", "~/.gnupg/pubring.kbx", "~/Library/Cookies/x"):
        with pytest.raises(PermissionError, match="credential"):
            f.write(target, "nope")
    # Read access is unrestricted, but the test does not assume ~/.ssh exists.
    assert f.path("~/.ssh") == (Path.home() / ".ssh").resolve()


def test_credential_parent_cannot_be_moved(files, monkeypatch):
    import macos_local_mcp.files as module
    f, _guard, tmp = files
    parent = tmp / "config"
    (parent / "gcloud").mkdir(parents=True)
    monkeypatch.setattr(module, "PROTECTED_WRITE_PREFIXES", (str(parent / "gcloud"),))
    with pytest.raises(PermissionError, match="protected directory"):
        f.move(str(parent), str(tmp / "relocated"))
    assert (parent / "gcloud").exists()


def test_move_same_volume_still_works(files):
    f, _guard, tmp = files
    src, dst = tmp / "s.txt", tmp / "d.txt"
    f.write(str(src), "data")
    result = f.move(str(src), str(dst))
    assert "cross_volume" not in result
    assert f.read_text(str(dst))["text"] == "data"


def test_tool_definitions_are_unique_and_keep_required_version(files):
    f, guard, tmp = files
    tree = ast.parse((PROJECT / "src/macos_local_mcp/server.py").read_text(encoding="utf-8"))
    build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_server")
    names = [n.name for n in build.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert len(names) == len(set(names))
    server, runtime = build_server(guard)
    try:
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        assert {"old_text", "new_text", "expected_version"} <= set(tools["edit_text_file"].inputSchema["required"])
        assert {"root", "query"} <= set(tools["search_text"].inputSchema["required"])
        assert "is_regex" not in tools["search_text"].inputSchema["properties"]
        assert "old_string" not in INSTRUCTIONS
    finally:
        runtime.commands.close()
