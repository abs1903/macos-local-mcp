"""Exercise actual EXDEV fallback, exclusive publication and recoverable failures."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil

import pytest
import send2trash

from macos_local_mcp.files import Files
from macos_local_mcp.guard import Guard
import macos_local_mcp.moves as moves


@pytest.fixture
def files(tmp_path):
    return Files(Guard(tmp_path / "state"))


def force_cross_volume(monkeypatch, source):
    original = moves.rename_noreplace
    calls = []
    def rename(src, dst):
        calls.append((Path(src), Path(dst)))
        if Path(src) == source:
            raise OSError(errno.EXDEV, "synthetic cross-volume boundary")
        return original(src, dst)
    monkeypatch.setattr(moves, "rename_noreplace", rename)
    return calls


def fake_trash(monkeypatch, tmp_path):
    calls = []
    def recycle(value):
        source = Path(value)
        calls.append(source)
        source.rename(tmp_path / ("trashed-" + source.name))
    monkeypatch.setattr(send2trash, "send2trash", recycle)
    return calls


@pytest.mark.parametrize("directory", [False, True])
def test_exdev_copies_then_recycles_original(files, tmp_path, monkeypatch, directory):
    source, destination = tmp_path / "source", tmp_path / "destination"
    if directory:
        (source / "sub").mkdir(parents=True)
        (source / "sub/data.txt").write_bytes(b"nested payload")
    else:
        source.write_bytes(b"file payload")
    calls = force_cross_volume(monkeypatch, source)
    recycled = fake_trash(monkeypatch, tmp_path)
    result = files.move(str(source), str(destination))
    assert len(calls) == 2  # initial EXDEV, then staging publication
    assert result["cross_volume"] and result["source_recycled"]
    assert recycled == [source] and not source.exists()
    assert (destination / "sub/data.txt").read_bytes() == b"nested payload" if directory else destination.read_bytes() == b"file payload"
    assert not list(tmp_path.glob(".mcp-move-*"))


@pytest.mark.parametrize("directory", [False, True])
def test_exclusive_rename_never_clobbers_existing_destination(tmp_path, directory):
    source, destination = tmp_path / "source", tmp_path / "destination"
    if directory:
        source.mkdir()
        destination.mkdir()
    else:
        source.write_bytes(b"source")
        destination.write_bytes(b"original destination")
    with pytest.raises(FileExistsError):
        moves.rename_noreplace(source, destination)
    assert source.exists() and destination.exists()
    if not directory:
        assert destination.read_bytes() == b"original destination"


def test_destination_created_during_copy_is_not_overwritten(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    force_cross_volume(monkeypatch, source)
    recycled = fake_trash(monkeypatch, tmp_path)
    copy = moves._copy_file
    def racing_copy(*args):
        result = copy(*args)
        destination.write_bytes(b"concurrent writer")
        return result
    monkeypatch.setattr(moves, "_copy_file", racing_copy)
    with pytest.raises(FileExistsError):
        files.move(str(source), str(destination))
    assert source.read_bytes() == b"source content"
    assert destination.read_bytes() == b"concurrent writer"
    assert recycled == [] and not list(tmp_path.glob(".mcp-move-*"))


def test_pause_during_copy_preserves_source(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    force_cross_volume(monkeypatch, source)
    recycled = fake_trash(monkeypatch, tmp_path)
    copy = moves._copy_file
    def pause_after_copy(*args):
        result = copy(*args)
        files.guard.pause()
        return result
    monkeypatch.setattr(moves, "_copy_file", pause_after_copy)
    with pytest.raises(PermissionError, match="paused"):
        files.move(str(source), str(destination))
    assert source.exists() and not destination.exists() and recycled == []
    assert not list(tmp_path.glob(".mcp-move-*"))


def test_source_changed_during_copy_is_not_recycled(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    force_cross_volume(monkeypatch, source)
    recycled = fake_trash(monkeypatch, tmp_path)
    copy = moves._copy_file
    def change_after_copy(*args):
        result = copy(*args)
        source.write_bytes(b"new live content")
        return result
    monkeypatch.setattr(moves, "_copy_file", change_after_copy)
    with pytest.raises(ValueError, match="changed during copy"):
        files.move(str(source), str(destination))
    assert source.read_bytes() == b"new live content"
    assert not destination.exists() and recycled == []


def test_trash_failure_returns_explicit_partial_success(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    force_cross_volume(monkeypatch, source)
    def failed_trash(value):
        raise OSError("simulated unavailable Trash")
    monkeypatch.setattr(send2trash, "send2trash", failed_trash)
    result = files.move(str(source), str(destination))
    assert result["cross_volume"] and result["source_recycled"] is False
    assert "both copies retained" in result["warning"]
    assert source.read_bytes() == destination.read_bytes() == b"source content"


def test_cross_volume_refuses_metadata_before_copy(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    force_cross_volume(monkeypatch, source)
    monkeypatch.setattr(moves, "_has_xattrs", lambda p: True)
    with pytest.raises(ValueError, match="special metadata"):
        files.move(str(source), str(destination))
    assert source.exists() and not destination.exists()


def test_cross_volume_refuses_hardlinks(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source content")
    try:
        os.link(source, tmp_path / "alias")
    except OSError as exc:
        pytest.skip(f"Hardlinks unavailable: {exc}")
    force_cross_volume(monkeypatch, source)
    with pytest.raises(ValueError, match="multiply linked"):
        files.move(str(source), str(destination))
    assert source.exists() and not destination.exists()


def test_cross_volume_refuses_nested_symlink(files, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"do not follow")
    try:
        (source / "link").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    force_cross_volume(monkeypatch, source)
    with pytest.raises(ValueError, match="symlinks"):
        files.move(str(source), str(destination))
    assert outside.read_bytes() == b"do not follow"
    assert source.exists() and not destination.exists()


def test_destination_inside_source_is_rejected(files, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="inside the source"):
        files.move(str(source), str(source / "nested"))
