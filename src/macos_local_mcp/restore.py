"""Local-only content restoration. This module is never registered as an MCP tool."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .files import Files, MAX_WRITE
from .guard import Guard


def backup_records(state: Path) -> list[tuple[int, Path, dict]]:
    backups = state / "backups"
    if not backups.is_dir() or backups.is_symlink():
        return []
    records = []
    for folder in backups.iterdir():
        try:
            meta, payload = folder / "metadata.json", folder / "original.bin"
            if (folder.is_symlink() or not folder.is_dir() or meta.is_symlink()
                    or payload.is_symlink() or not payload.is_file()):
                continue
            if meta.stat().st_size > 65536:
                continue
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("original_path"), str):
                continue
            if not Path(data["original_path"]).is_absolute():
                continue
            st = folder.stat()
            created = getattr(st, "st_birthtime_ns", st.st_mtime_ns)
            records.append((created, folder, data))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: (item[0], item[1].name), reverse=True)


def restore_record(files: Files, record: tuple[int, Path, dict], confirm=input) -> dict:
    _created, folder, data = record
    original = files.path(data["original_path"], mutation=True)
    saved = folder / "original.bin"
    if (folder.is_symlink() or folder.resolve().parent != files.guard.state / "backups"
            or saved.is_symlink() or not saved.is_file()):
        raise ValueError("Backup must be a regular payload in the private backup directory")
    before = files.info(str(original)) if original.exists() else None
    if before is not None:
        try:
            answer = confirm(f"{original} already exists. Back it up and overwrite? [y/N] ")
        except EOFError as exc:
            raise ValueError("Confirmation requires interactive input; nothing was changed") from exc
        if answer.strip().lower() != "y":
            raise ValueError("Aborted; nothing was changed")
    with saved.open("rb") as stream:
        content = stream.read(MAX_WRITE + 1)
    if len(content) > MAX_WRITE:
        raise ValueError("Backup exceeds the 8 MiB restore limit; restore it with local filesystem tools")
    files.guard.check()
    if not original.parent.exists():
        files.mkdir(str(original.parent))
    # Version check and automatic backup preserve the live file if anything changed
    # while the operator was confirming. Unsupported metadata is refused, not lost.
    return files._write_bytes(str(original), content, overwrite=before is not None,
                              expected_version=before["version"] if before else None)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    guard = Guard()
    records = backup_records(guard.state)
    if not args or args == ["list"]:
        print(f"{len(records)} backup(s), newest first:")
        for index, (_created, folder, data) in enumerate(records, 1):
            print(f"[{index}] {data['original_path']}\n     saved file: {folder / 'original.bin'}")
        print("Restore content with: ./Restore.command restore <backup-index>")
        return
    if len(args) != 2 or args[0] != "restore":
        raise SystemExit("Usage: ./Restore.command [list] | restore <backup-index>")
    try:
        index = int(args[1])
        if not 1 <= index <= len(records):
            raise ValueError("Index is outside the backup list")
        result = restore_record(Files(guard), records[index - 1])
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Restored content to {result['path']}.")
    if result["backup_path"]:
        print(f"Previous live content backed up to {result['backup_path']}.")
    print("Historical ownership, ACLs and timestamps are not restored.")


if __name__ == "__main__":
    main()
