"""Local-only operational helpers: no real Keychain, launchd or live files used."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from macos_local_mcp.files import Files
from macos_local_mcp.guard import Guard, PROJECT
from macos_local_mcp.restore import backup_records, restore_record
import macos_local_mcp.launchd as launchd


@pytest.fixture
def backed_up(tmp_path):
    files = Files(Guard(tmp_path / "state"))
    path = tmp_path / "document.txt"
    files.write(str(path), "original\r\n")
    files.write(str(path), "current\r\n", overwrite=True)
    record = backup_records(files.guard.state)[0]
    return files, path, record


def test_restore_backs_up_live_content_and_preserves_bytes(backed_up):
    files, path, record = backed_up
    prompts = []
    def confirm(prompt):
        prompts.append(prompt)
        return "y"
    result = restore_record(files, record, confirm)
    assert len(prompts) == 1
    assert path.read_bytes() == b"original\r\n"
    assert Path(result["backup_path"]).read_bytes() == b"current\r\n"
    assert len(backup_records(files.guard.state)) == 2


@pytest.mark.parametrize("answer", ["n", "", "yes"])
def test_restore_requires_exact_confirmation(backed_up, answer):
    files, path, record = backed_up
    with pytest.raises(ValueError, match="Aborted"):
        restore_record(files, record, lambda prompt: answer)
    assert path.read_bytes() == b"current\r\n"


def test_restore_eof_does_not_overwrite(backed_up):
    files, path, record = backed_up
    def eof(prompt):
        raise EOFError
    with pytest.raises(ValueError, match="interactive input"):
        restore_record(files, record, eof)
    assert path.read_bytes() == b"current\r\n"


def test_restore_refuses_change_during_confirmation(backed_up):
    files, path, record = backed_up
    def changed(prompt):
        path.write_bytes(b"newer external content")
        return "y"
    with pytest.raises(ValueError, match="changed since"):
        restore_record(files, record, changed)
    assert path.read_bytes() == b"newer external content"


def test_restore_does_not_expose_protected_credential_writes(backed_up, monkeypatch):
    import macos_local_mcp.files as module
    files, path, record = backed_up
    monkeypatch.setattr(module, "PROTECTED_WRITE_PREFIXES", (str(path.parent),))
    with pytest.raises(PermissionError, match="credential"):
        restore_record(files, record, lambda prompt: "y")
    assert path.read_bytes() == b"current\r\n"


def test_backup_browser_skips_malformed_records(backed_up):
    files, _path, record = backed_up
    folder = files.guard.state / "backups" / "bad"
    folder.mkdir()
    (folder / "original.bin").write_bytes(b"not a valid record")
    meta = folder / "metadata.json"
    for value in ([], {}, {"original_path": "relative"}, {"original_path": 123}):
        meta.write_text(json.dumps(value), encoding="utf-8")
        assert backup_records(files.guard.state) == [record]


def test_restore_missing_original_is_created_without_confirmation(backed_up):
    files, path, record = backed_up
    path.rename(path.with_suffix(".held"))
    def unexpected_confirmation(prompt):
        raise AssertionError("Missing destination must not prompt")
    result = restore_record(files, record, unexpected_confirmation)
    assert path.read_bytes() == b"original\r\n"
    assert result["backup_path"] is None


def test_shell_wrappers_leave_stdin_and_use_exec_handoff():
    restore = (PROJECT / "Restore.command").read_text(encoding="utf-8")
    daemon = (PROJECT / "LaunchDaemon.command").read_text(encoding="utf-8")
    assert 'exec "$PYTHON" -m macos_local_mcp.restore "$@"' in restore
    assert "<<" not in restore
    assert "sleep 0.2" not in daemon
    assert 'exec "$PYTHON" -m macos_local_mcp.launchd' in daemon


def launch_paths(tmp_path):
    state = tmp_path / "state"
    tools = state / "tools"
    tools.mkdir(parents=True)
    executable = tools / "fake-tunnel"
    executable.write_text("not executed", encoding="utf-8")
    executable.chmod(0o700)
    return state, executable, state / "health-test.url"


def test_launch_record_targets_intended_executable_before_exec(tmp_path, monkeypatch):
    state, executable, health = launch_paths(tmp_path)
    seen = []
    def fake_exec(exe, argv):
        record = json.loads((state / "running.json").read_text(encoding="utf-8"))
        seen.append(record)
        assert record["pid"] == os.getpid()
        assert record["executable"] == str(executable.resolve())
        assert record["health_file"] == str(health.resolve())
        assert exe == str(executable.resolve()) and argv[0] == exe
        assert "--health.url-file" in argv and str(health.resolve()) in argv
    monkeypatch.setattr(launchd.os, "execv", fake_exec)
    monkeypatch.setattr(launchd.os, "access", lambda *args: True)
    launchd.exec_tunnel(str(executable), str(health), state)
    assert len(seen) == 1 and not list(state.glob(".running-*"))


def test_failed_exec_removes_only_its_own_record(tmp_path, monkeypatch):
    state, executable, health = launch_paths(tmp_path)
    def failed_exec(*args):
        raise OSError("simulated exec failure")
    monkeypatch.setattr(launchd.os, "execv", failed_exec)
    monkeypatch.setattr(launchd.os, "access", lambda *args: True)
    with pytest.raises(OSError, match="simulated"):
        launchd.exec_tunnel(str(executable), str(health), state)
    assert not (state / "running.json").exists()
    assert not list(state.glob(".running-*"))


def test_failed_exec_keeps_a_newer_instances_record(tmp_path, monkeypatch):
    state, executable, health = launch_paths(tmp_path)
    newer = {"pid": 123, "newer_instance": True}
    def competing_exec(*args):
        (state / "running.json").write_text(json.dumps(newer), encoding="utf-8")
        raise OSError("simulated failure")
    monkeypatch.setattr(launchd.os, "execv", competing_exec)
    monkeypatch.setattr(launchd.os, "access", lambda *args: True)
    with pytest.raises(OSError):
        launchd.exec_tunnel(str(executable), str(health), state)
    assert json.loads((state / "running.json").read_text()) == newer


def test_launch_health_path_is_bounded_to_private_state(tmp_path, monkeypatch):
    state, executable, _health = launch_paths(tmp_path)
    monkeypatch.setattr(launchd.os, "access", lambda *args: True)
    with pytest.raises(ValueError, match="Health file"):
        launchd.exec_tunnel(str(executable), str(tmp_path / "outside.url"), state)
    assert not (state / "running.json").exists()
