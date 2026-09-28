"""Local LaunchAgent exec handoff with a verifiable, atomic process record."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import psutil


def exec_tunnel(tunnel: str, health_file: str, state: Path) -> None:
    state = state.resolve()
    executable = Path(tunnel).resolve(strict=True)
    if (not executable.is_file() or not os.access(executable, os.X_OK)
            or not executable.is_relative_to(state / "tools")):
        raise ValueError("Tunnel must be an installed executable under the private tools directory")
    health = Path(health_file).resolve()
    if health.parent != state or not health.name.startswith("health-"):
        raise ValueError("Health file must be a per-start file in the private state directory")
    process = psutil.Process(os.getpid())
    record = {"pid": process.pid, "create_time": process.create_time(),
              "executable": str(executable), "health_file": str(health)}
    target = state / "running.json"
    fd, name = tempfile.mkstemp(prefix=".running-", suffix=".json", dir=state)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        # exec preserves PID/create_time. Record the intended executable BEFORE
        # handoff, not the shell/python executable after an arbitrary sleep.
        os.execv(str(executable), [str(executable), "run",
                 "--health.listen-addr", "127.0.0.1:0", "--health.url-file", str(health),
                 "--mcp.stdio-send-initialized-notification"])
    except BaseException:
        try:
            if json.loads(target.read_text(encoding="utf-8")) == record:
                target.unlink()
        except (OSError, ValueError):
            pass
        raise
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Use LaunchDaemon.command from the installed checkout")
    exec_tunnel(sys.argv[1], sys.argv[2], Path.cwd() / ".local")


if __name__ == "__main__":
    main()
