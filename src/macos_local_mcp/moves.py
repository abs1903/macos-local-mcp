"""No-clobber moves, with conservative copy-then-Trash across filesystems.

Cross-volume copies intentionally refuse links, special files and metadata that
cannot be preserved by this implementation. Failure retains the source. This is
not a filesystem transaction against a hostile concurrent writer.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from .files import MAX_READ, _has_acl, _has_xattrs, _same_file, _version


def rename_noreplace(source: Path, destination: Path) -> None:
    """Use kernel exclusive-rename semantics, not exists()+POSIX rename()."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows already refuses replacement.
        return
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = library.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        # Apple xnu bsd/sys/stdio.h: RENAME_EXCL = 0x00000004.
        result = function(os.fsencode(source), os.fsencode(destination), 4)
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renameat2", None)
        if function is None:
            raise OSError(errno.ENOTSUP, "Exclusive rename is unavailable")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                             ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOTSUP, "Exclusive rename is unavailable")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _snapshot(files, source: Path) -> dict[str, str]:
    """Check ordinary metadata and retain a bounded source manifest."""
    result = {}
    pending = [(source, 0)]
    while pending:
        files.guard.check()
        path, depth = pending.pop()
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400):
            raise ValueError("Cross-volume moves refuse symlinks and junctions")
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            raise ValueError("Cross-volume moves require ordinary files/directories")
        files.path(str(path), mutation=True)
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            raise ValueError("Cross-volume moves refuse multiply linked files")
        if (getattr(st, "st_flags", 0) or _has_xattrs(path)
                or (sys.platform == "darwin" and _has_acl(path))):
            raise ValueError("Cross-volume move could discard special metadata; use local filesystem tools")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise ValueError("Cross-volume move would change ownership; use local filesystem tools")
        result[str(path.relative_to(source))] = _version(st)
        if len(result) > 100000 or depth > 64:
            raise ValueError("Cross-volume tree exceeds the 100000-entry / 64-depth safety limit")
        if stat.S_ISDIR(st.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    files.guard.check()
                    if len(pending) + len(result) >= 100000:
                        raise ValueError("Cross-volume tree exceeds the 100000-entry safety limit")
                    pending.append((Path(entry.path), depth + 1))
    return result


def _copy_file(files, source: str | Path, destination: str | Path) -> str:
    source, destination = Path(source), Path(destination)
    files.guard.check()
    before_path = source.lstat()
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError("Source changed before copying")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(source, flags)
    with os.fdopen(fd, "rb") as reader, destination.open("xb") as writer:
        before = os.fstat(reader.fileno())
        if not _same_file(before, before_path) or not stat.S_ISREG(before.st_mode):
            raise ValueError("Source changed while opening")
        remaining = before.st_size
        while remaining:
            files.guard.check()
            block = reader.read(min(MAX_READ, remaining))
            if not block:
                raise ValueError("Source changed while copying")
            writer.write(block)
            remaining -= len(block)
        writer.flush()
        os.fsync(writer.fileno())
        if (_version(os.fstat(reader.fileno())) != _version(before)
                or _version(source.lstat()) != _version(before_path)):
            raise ValueError("Source changed while copying")
    shutil.copystat(source, destination, follow_symlinks=False)
    return str(destination)


def cross_volume_move(files, source: Path, destination: Path) -> dict:
    from send2trash import send2trash
    before = _snapshot(files, source)
    files.guard.check()
    # A private directory owns the staging path until exclusive publication.
    with tempfile.TemporaryDirectory(prefix=".mcp-move-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        if source.is_dir():
            shutil.copytree(source, staged, symlinks=True,
                            copy_function=lambda src, dst: _copy_file(files, src, dst))
        else:
            _copy_file(files, source, staged)
        files.guard.check()
        if _snapshot(files, source) != before:
            raise ValueError("Source changed during copy; original left untouched")
        rename_noreplace(staged, destination)
    files.guard.check()
    if _snapshot(files, source) != before:
        raise ValueError("Source changed after publication; both source and destination retained")
    result = {"source": str(source), "destination": str(destination), "cross_volume": True}
    try:
        files.guard.check()
        send2trash(str(source))
    except OSError:
        files.guard.check()  # A pause is an error, not a successful partial move.
        return {**result, "source_recycled": False,
                "warning": "Copy complete, but Trash failed; both copies retained. No permanent deletion attempted."}
    return {**result, "source_recycled": True}
