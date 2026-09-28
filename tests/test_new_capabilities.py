"""Window-target regressions and real macOS Seatbelt enforcement."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys

import pytest

from test_desktop import FakeBackend
from macos_local_mcp.commands import Commands, PosixProcess, _private_environment, seatbelt_prefix, SEATBELT_PROFILES
from macos_local_mcp.control import set_commands_enabled
from macos_local_mcp.desktop import Desktop, DesktopError
from macos_local_mcp.guard import Guard

native = pytest.mark.skipif(sys.platform != "darwin", reason="Real Seatbelt runs on macOS CI")


def locked_window():
    backend = FakeBackend()
    desktop = Desktop(lambda: None, backend=backend)
    desktop.focus_window(10)
    return desktop, backend


def test_click_inside_window_bounds_passes():
    desktop, backend = locked_window()
    desktop.click(400, 300)
    assert ("button", 400, 300, "left", True) in backend.events


@pytest.mark.parametrize("x, y", [(850, 300), (400, 650), (-10, 100)])
def test_click_outside_window_bounds_is_rejected(x, y):
    desktop, backend = locked_window()
    with pytest.raises(DesktopError, match="outside the locked target window"):
        desktop.click(x, y)
    assert not any(event[0] in ("button", "move") for event in backend.events)


def test_drag_endpoints_must_both_be_inside():
    desktop, _backend = locked_window()
    with pytest.raises(DesktopError, match="outside"):
        desktop.drag(10, 10, 900, 10, 0.05)


def test_scroll_and_move_check_bounds_too():
    desktop, backend = locked_window()
    with pytest.raises(DesktopError, match="outside"):
        desktop.scroll(1700, 1000, vertical=2)
    with pytest.raises(DesktopError, match="outside"):
        desktop.move(1000, 100)
    assert not any(event[0] in ("scroll", "move") for event in backend.events)


def test_modal_pointer_input_uses_dialog_bounds_not_blanket_exemption():
    desktop, backend = locked_window()
    backend.focused = {"window_id": 11, "title": "Save", "modal": True}
    backend._windows.append({"window_id": 11, "pid": 100, "left": 900,
                             "top": 100, "right": 1200, "bottom": 500})
    desktop.click(1000, 200)
    backend.events.clear()
    with pytest.raises(DesktopError, match="outside"):
        desktop.click(100, 200)
    assert backend.events == []


def test_unknown_modal_bounds_fail_closed():
    desktop, backend = locked_window()
    backend.focused = {"window_id": 0, "title": "Save", "modal": True}
    with pytest.raises(DesktopError, match="modal window bounds"):
        desktop.click(1000, 200)
    assert backend.events == []


def test_window_scoped_screenshot_uses_window_capture():
    desktop, backend = locked_window()
    data, meta = desktop.screenshot(target_window=True)
    assert data.startswith(b"\x89PNG") and meta["window_scoped"]
    assert backend.events[-1] == ("capture_window", 10)
    assert desktop.screenshot()[1]["window_scoped"] is False


def test_window_scoped_screenshot_requires_target():
    desktop = Desktop(lambda: None, backend=FakeBackend())
    with pytest.raises(DesktopError, match="No desktop input target"):
        desktop.screenshot(target_window=True)


@pytest.mark.parametrize("changed", ["owner", "process"])
def test_window_capture_rejects_reused_target_before_reading_pixels(changed):
    desktop, backend = locked_window()
    if changed == "owner":
        backend._windows[0]["pid"] = 999
    else:
        backend.identity["create_time"] += 10
    with pytest.raises(DesktopError, match="changed"):
        desktop.screenshot(target_window=True)
    assert not any(event[0].startswith("capture") for event in backend.events)


def test_window_capture_rejects_changed_geometry():
    desktop, backend = locked_window()
    capture = backend.capture_window_png
    def moving_capture(window):
        data = capture(window)
        backend._windows[0]["right"] += 20
        return data
    backend.capture_window_png = moving_capture
    with pytest.raises(DesktopError, match="changed during capture"):
        desktop.screenshot(target_window=True)


def test_window_capture_rejects_region_instead_of_ignoring_it():
    desktop, _backend = locked_window()
    with pytest.raises(ValueError, match="cannot be combined"):
        desktop.screenshot((0, 0, 100, 100), target_window=True)


def test_bounds_are_refetched_when_window_moves():
    desktop, backend = locked_window()
    backend._windows[0]["left"] = 100
    backend._windows[0]["right"] = 900
    desktop.click(850, 300)
    with pytest.raises(DesktopError):
        desktop.click(50, 300)


def test_drag_rechecks_computed_points_when_window_moves():
    desktop, backend = locked_window()
    original_move = backend.mouse_move
    def move_then_shrink(x, y):
        original_move(x, y)
        backend._windows[0]["right"] = 100
    backend.mouse_move = move_then_shrink
    with pytest.raises(DesktopError, match="outside the locked target window"):
        desktop.drag(10, 10, 400, 400, 0.05)
    assert any(event[0] == "button" and event[4] is False for event in backend.events)


def test_drag_revalidates_after_sleep(monkeypatch):
    import macos_local_mcp.desktop as module
    desktop, backend = locked_window()
    real_sleep = module.time.sleep
    def moving_sleep(seconds):
        if any(event[0] == "button" and event[4] is True for event in backend.events):
            backend._windows[0]["right"] = 100
        real_sleep(seconds)
    monkeypatch.setattr(module.time, "sleep", moving_sleep)
    with pytest.raises(DesktopError, match="outside"):
        desktop.drag(10, 10, 400, 400, 0.05)
    assert not any(event[0] == "move" and event[1] > 100 for event in backend.events)
    assert backend.events[-1][0] == "button" and backend.events[-1][4] is False


def test_env_filter_blocks_known_credentials_but_keeps_build_paths():
    for name in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "ANTHROPIC_API_KEY", "STRIPE_KEY", "OPENAI_API_KEY"):
        assert _private_environment(name), name
    for name in ("PATH", "HOME", "LANG", "VIRTUAL_ENV", "SHELL", "TMPDIR", "SSH_AUTH_SOCK"):
        assert not _private_environment(name), name


def test_sandbox_and_network_validation(tmp_path):
    commands = Commands(Guard(tmp_path / "state"))
    try:
        with pytest.raises(ValueError, match="sandbox"):
            commands.start("/usr/bin/true", [], "/tmp", sandbox="unknown")
        with pytest.raises(ValueError, match="network"):
            commands.start("/usr/bin/true", [], "/tmp", network=True)
        with pytest.raises(ValueError, match="boolean"):
            commands.start("/usr/bin/true", [], "/tmp", sandbox="read-only", network="yes")
        assert commands.status()["sandbox_profiles"] == sorted(SEATBELT_PROFILES)
    finally:
        commands.close()


def run_sandbox(cwd, profile, code, network=False):
    prefix = seatbelt_prefix(str(cwd.resolve()), profile, network=network)
    profile_path = Path(prefix[2])
    proc = PosixProcess(sys.executable, ["-c", code], str(cwd.resolve()), dict(os.environ), sandbox_argv=prefix)
    try:
        out, err = proc.process.communicate(timeout=15)
        return proc.process.returncode, out.decode(errors="replace"), err.decode(errors="replace")
    finally:
        if proc.process.poll() is None:
            proc.process.kill()
            proc.process.wait(timeout=5)
        proc.close()
        assert not profile_path.exists()


@native
def test_seatbelt_read_only_blocks_writes_and_cleans_profile(tmp_path):
    target = tmp_path / "blocked.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('no')"
    status, _out, _err = run_sandbox(tmp_path, "read-only", code)
    assert status != 0 and not target.exists()


@native
def test_seatbelt_workspace_write_allows_cwd_blocks_sibling(tmp_path):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    inside, outside = cwd / "inside.txt", tmp_path / "outside.txt"
    code = f"from pathlib import Path; Path({str(inside)!r}).write_text('yes'); Path({str(outside)!r}).write_text('no')"
    # Use /tmp rather than Darwin's per-user temp directory, which the documented
    # profile intentionally permits. A sibling under /tmp must stay read-only.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mcp-seatbelt-", dir="/tmp") as parent:
        cwd = Path(parent) / "workspace"
        cwd.mkdir()
        inside, outside = cwd / "inside.txt", Path(parent) / "outside.txt"
        code = f"from pathlib import Path; Path({str(inside)!r}).write_text('yes'); Path({str(outside)!r}).write_text('no')"
        status, _out, _err = run_sandbox(cwd, "workspace-write", code)
        assert status != 0 and inside.read_text() == "yes" and not outside.exists()


@native
@pytest.mark.parametrize("network", [False, True])
def test_seatbelt_network_policy_with_loopback_not_dns(tmp_path, network):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        code = f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=3).close(); print('connected')"
        status, out, _err = run_sandbox(tmp_path, "workspace-write", code, network=network)
    assert (status == 0 and "connected" in out) is network


@native
def test_seatbelt_rejects_quote_in_cwd():
    with pytest.raises(ValueError, match="quote or backslash"):
        seatbelt_prefix('/tmp/bad"quote', "workspace-write")


@pytest.mark.parametrize("character", ["\n", "\r", "\x00"])
def test_seatbelt_rejects_control_characters(character):
    with pytest.raises(ValueError, match="control characters"):
        seatbelt_prefix("/tmp/a" + character + "b", "workspace-write")


@native
def test_sandbox_profile_removed_after_actual_launch_failure(tmp_path, monkeypatch):
    import macos_local_mcp.commands as module
    commands = Commands(Guard(tmp_path / "state"))
    set_commands_enabled(commands.guard, True)
    paths = []
    prefix = module.seatbelt_prefix
    def tracking_prefix(*args, **kwargs):
        result = prefix(*args, **kwargs)
        paths.append(Path(result[2]))
        assert paths[-1].exists()
        return result
    def failed_launch(*args, **kwargs):
        raise OSError("synthetic launch failure after profile creation")
    monkeypatch.setattr(module, "seatbelt_prefix", tracking_prefix)
    monkeypatch.setattr(module.subprocess, "Popen", failed_launch)
    try:
        with pytest.raises(OSError, match="synthetic launch failure"):
            commands.start("/usr/bin/true", [], str(tmp_path), sandbox="read-only")
        assert paths and all(not path.exists() for path in paths)
        assert not commands.jobs
    finally:
        commands.close()
