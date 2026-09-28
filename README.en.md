# macOS Local MCP

**English** · [简体中文](README.zh-CN.md)

Give ChatGPT in Chat mode MCP-based access to local files, file writes, screen viewing, and macOS desktop control. The MCP surface is intentionally close to the Windows version, while the desktop backend uses Quartz, the macOS Accessibility API, and macOS privacy permissions.

Current version: 0.3.0.

## Permissions and risks

This is a **high-privilege local tool**. Once enabled, a model can read and modify files within the permissions of the current macOS user. If you also grant Accessibility and Screen Recording, it can observe the screen and send mouse and keyboard input to ordinary desktop applications.

Important risks and boundaries:

- file access is close to the current macOS user's own access, not an isolated sandbox;
- **Accessibility** is a high-privilege permission that lets the host process control other ordinary applications;
- **Screen Recording** can expose other application windows and screen contents, including chats, mail, signed-in pages, filenames, and other private information;
- target locking, one-use observation IDs, local pause, backups, and audit logs reduce mistakes but **do not turn the model into a low-privilege process**;
- already-posted input events and completed disk writes cannot be automatically undone;
- macOS TCC permissions are usually granted to the actual host running Python/MCP (for example Terminal, iTerm, or another launcher), so the permission scope can be broader than one Python file;
- runtime keys, Tunnel credentials, .local state, audit records, and private screenshots must not be committed to GitHub or pasted into chat;
- if you are not comfortable granting current-user file access and desktop-control capability, do not run this service.

Use a standard non-root user. Pause the service while handling password managers, payments, sensitive accounts, health/financial information, or important production data.

## API parity with the Windows version

The primary tools use the same names:

| Purpose | Tools |
| --- | --- |
| File metadata and directories | file_info, list_directory |
| Text and binary reads | read_text_file, read_binary_file |
| Recursive filename and text search | search_files, search_text |
| Exact partial edits with a version check | edit_text_file |
| Create, replace, mkdir, move, Trash | write_file, create_directory, move_path, recycle_path |
| Glob convenience and content verification | glob_files, file_hash |
| Displays, windows, screenshots | desktop_monitors, desktop_windows, desktop_screenshot |
| Focus a window and lock input | desktop_focus_window |
| Mouse | desktop_click, desktop_move, desktop_drag, desktop_scroll |
| Keyboard and Unicode text | desktop_keypress, desktop_type_text |
| Local command start, output/status and cancellation | command_start, command_poll, command_cancel |
| Permission status | desktop_permissions |
| Status and pause | service_status, service_pause |

Coordinates use **Quartz global points**, not raw Retina screenshot pixels. desktop_screenshot returns scale_x / scale_y for mapping output-image coordinates back to desktop coordinates.

## Local command execution (0.2.0)

Use `command_start` to launch a program, `command_poll` to read output/status, and `command_cancel` to stop a job. No Terminal focus, Accessibility, or Screen Recording permission is needed. Access to TCC-protected files still depends on the host's existing permissions.

Commands are disabled by default. Run `./Enable-Commands.command` on the Mac and type `ENABLE`, or use the `Control.command` menu. `./Disable-Commands.command` revokes approval and stops running command groups. Enabling does not resume a paused service or restart cancelled jobs. Explicit local administration can use `.venv/bin/python -m macos_local_mcp.control enable-commands --accept-command-risk`.

Example `command_start` arguments for an installed Git:

```json
{
  "executable": "/usr/bin/git",
  "arguments": ["--version"],
  "cwd": "/tmp",
  "timeout_seconds": 30
}
```

Poll the returned `job_id` with `command_poll`; pass the same ID to `command_cancel` to cancel it. **Starting is not success**: only `state=completed` with `exit_code=0` is successful. Nonzero exits, timeout, pause, cancellation and revoked approval remain distinct outcomes.

`sandbox` selects an optional macOS Seatbelt (`sandbox-exec`) profile. `workspace-write` permits writes in the resolved working directory and Darwin per-user temporary directories; `read-only` denies ordinary filesystem writes. Both retain standard-device access (including `/dev/null`) and deny network by default. Explicit `network=true` permits network with either profile. `sandbox=None` keeps current-user permissions. Profiles are stored in system temp and removed on normal cleanup; force-killing the service may leave a profile. This is not a hard security boundary. A denylist strips known credential/service environment variables, not every possible secret. `SSH_AUTH_SOCK` is deliberately retained for Git/SSH and grants access to the user's agent; network opt-in does not eliminate credential risk. Do not run untrusted commands merely because a profile is selected.

Executable and cwd must be absolute paths; arguments must be an array. No shell is implicitly inserted, so spaces, semicolons, `$()` and globs are literal. Explicitly authorize a shell such as `/bin/zsh` and its script arguments when pipelines or redirects are required. `environment` can supply build variables; known Tunnel/API credentials and service-private variables are stripped and cannot be overridden. Executable symlink invocation paths are preserved for Homebrew and virtual environments.

At most 4 jobs run concurrently and 32 jobs are retained in memory. Timeout defaults to 600 seconds and accepts 1–86400. stdout/stderr are incrementally decoded and capped at 262144 Unicode characters each; pipes keep draining after truncation. Polling returns at most 65536 characters per stream. Offsets count characters: follow each stream's `next_offset` and inspect `truncated`. `wait_seconds` is 0–10 and never holds the global action lock. Encodings: UTF-8, GB18030, UTF-16LE and CP1252; invalid byte sequences use replacement characters.

Jobs run in their own POSIX process groups, with stdin at EOF and no interactive terminal, sudo password input or automatic elevation. Normal root exit cleans up remaining children in the group; timeout, cancellation, pause, revoked approval and normal service shutdown also stop the group. Cancellation remains available while paused. **Cancellation is not rollback**; command file writes do not receive `write_file` backups. Deliberately detached processes, launchd-managed processes, and force-killing the MCP process are outside guaranteed cleanup. This is not a daemon manager or an OS sandbox.

Command entry points/cwd reject the service source and private state directories, but arbitrary script internals cannot be isolated by path checks. Never bypass tool refusals, pause, macOS permissions or service protections. Output is memory-only; audit records exclude arguments, environment values, paths and output contents. Output can contain sensitive data and must be treated as untrusted.

## Desktop target lock

Desktop input does not automatically follow the user to a newly foregrounded application after another screenshot.

Flow:

1. find the intended window with desktop_windows;
2. explicitly lock it with desktop_focus_window;
3. later screenshots only observe and do not retarget;
4. if the user switches to another app, mouse, wheel, and keyboard input is rejected;
5. only another explicit desktop_focus_window call changes the target.

The target identity checks PID, process creation time, executable path, Bundle ID, and the current Accessibility focused window. The target window is accepted; a modal dialog in the same target app may be accepted; another ordinary window in the same app is not automatically accepted.

Every desktop input still requires a fresh one-use observation_id that expires after 60 seconds.

## macOS privacy permissions

Desktop features need:

- **Accessibility** for window activation and synthetic input;
- **Screen Recording** for screen and other-application window capture.

The MCP service never grants these permissions remotely. Permissions.command can only request/check the local system workflow. The human must approve permissions in **System Settings → Privacy & Security**.

After changing a permission, restart the Terminal/iTerm/host application and the MCP service as needed.

## Installation

Initial target:

- macOS 13 Ventura or later;
- Apple Silicon and Intel Macs;
- Python 3.13 or later;
- outbound HTTPS access to OpenAI and GitHub Releases.

On a physical Mac:

~~~bash
chmod +x *.command
./Setup.command
./Configure.command
./Permissions.command
./Start.command
~~~

Setup.command creates an isolated .venv, installs Python dependencies, and downloads the official OpenAI Tunnel client for the current CPU architecture, verifying the archive against the release SHA256SUMS.txt (mismatch deletes the download). Configure.command keeps the Tunnel ID in local private state and stores the runtime API key in macOS Keychain.

Useful local controls:

~~~text
Control.command      interactive local menu
Check.command        pause, permission, and Tunnel status
Pause.command        local pause
Resume.command       local resume
Stop.command         stop the Tunnel
Restore.command      browse/restore automatic file backups (local-only)
~~~

## File-safety semantics

- replacing a file requires overwrite=true;
- the original is backed up before replacement;
- expected_modified_ns can reject stale overwrites;
- symlinks are not mutation entry points;
- multiply linked, immutable, and extended-attribute-bearing files are refused by default to avoid losing special metadata during atomic replacement;
- deletion goes to Trash and never falls back to permanent deletion;
- writes, mkdir, move and Trash refuse well-known credential trees (~/.ssh, ~/.gnupg, ~/.aws, ~/Library/Keychains, ~/Library/Cookies) even though reads stay unrestricted;
- move_path uses exclusive, no-overwrite renames. Across volumes it copies ordinary files/trees, verifies the source, then uses Trash. Links, special files, hardlinked files and unsupported metadata are refused. If Trash fails, `source_recycled=false` explicitly reports that both copies remain; no permanent deletion is attempted. This is not a filesystem transaction against hostile concurrent changes.
- service source mutations are denied; private .local credentials/backups/audit data are denied for reads and writes. All search tools exclude private state.

### edit_text_file (preferred editing primitive)

`edit_text_file` requires `old_text`, `new_text` and the `expected_version` returned by `read_text_file` or `file_info`. Exactly one occurrence must match, including overlapping matches; there is no replace-all mode. It preserves untouched bytes, BOM and line endings in UTF-8, UTF-8-sig, UTF-16 and GB18030 files up to 8 MiB. Changed content is backed up; stale versions require rereading. See [file-tool details](docs/file-tools.md).

### search_text and glob_files

`search_text(root, query)` performs literal single-line search under an explicit directory root, case-sensitive by default; no regular expressions are evaluated. `search_files` searches basename globs. `glob_files(path, pattern)` is a convenience wrapper over the same bounded engine, returning `matches` (at most 500). Searches skip links and private state and enforce entry, time and output limits; text search also limits total bytes. Inspect `skipped`, `truncated`, `stop_reason` and `complete`, including when no matches are found.

### Window-scoped screenshots and input bounds

`desktop_screenshot(target_window=true)` captures only the locked window after checking process/window ownership; changed geometry or identity during capture is rejected. It cannot be combined with `region`. Pointer input must stay within the current target bounds. A modal dialog uses its own verified bounds, never a blanket exemption; unidentifiable modal bounds fail closed.

macOS has many ACL, File Provider, iCloud, sandbox-container, and third-party filesystem edge cases. The current version does not claim complete coverage of all special metadata semantics.

## Validation status

Automated coverage currently includes:

- file create/read/replace/backup and stale-write checks;
- edit_text_file uniqueness, mandatory versions, stale-edit rejection and encoding/newline preservation;
- search_text/glob_files bounds, junk-directory and state-directory skipping;
- credential-tree and ancestor protection; injected EXDEV moves, destination races, pause, source changes and Trash failure;
- symlink/xattr protections;
- pause and audit behavior;
- one-use and expiration behavior for observation IDs;
- screenshots not retargeting input;
- window-scoped capture requires a locked target;
- mouse input rejected outside the locked window bounds;
- rejecting input after the user switches apps;
- allowing input after returning to the target;
- modal dialogs in the target app;
- rejecting another ordinary window in the same app;
- invalidating the target after process restart;
- rechecking the target between typed characters;
- mouse-up cleanup when a drag is interrupted;
- Seatbelt read-only/workspace-write enforcement and profile cleanup (macOS);
- known-credential environment filtering, local restore confirmation/backup and atomic launch handoff records.

GitHub Actions run portable tests and a macOS runner, with checks for PyObjC / Quartz / AppKit and the native APIs used by this project.

Version 0.1.1 also hardens several real-Mac edge cases:

- screenshots no longer require Accessibility merely to determine the foreground window; with Screen Recording alone, capture can still succeed while input_allowed safely reports false;
- key chords explicitly set Quartz Command / Shift / Control / Option flags, improving reliability for real shortcuts such as Command+C and Command+S;
- before each desktop action starts, the service checks for user-held modifier keys or mouse buttons, waits up to one second, and requires 100 ms of stable release;
- Quartz windows are matched to Accessibility windows using AXWindowNumber when available, with titles used only as a fallback;
- Tunnel stop/status logic verifies PID, process creation time, and executable path, then terminates the verified process tree through psutil to reduce PID-reuse risk;
- the absence of a global pause hotkey is deliberate: it avoids adding an Input Monitoring permission surface. Use the local Pause.command for emergency pause; service_pause also remains available to the authenticated MCP caller.

**Still pending physical-Mac validation:** real TCC authorization, screenshots, TextEdit/Safari/Finder/VS Code window switching, Chinese/emoji input, modal dialogs, multi-display behavior, and lock-screen behavior.

See [VALIDATION.en.md](VALIDATION.en.md).

## Security boundary

This service is not an OS sandbox and should not run as root. Secure MCP Tunnel authenticates the remote connection; the local stdio entry point should only be used by a trusted local client or the official Tunnel process.

See [SECURITY.en.md](SECURITY.en.md).

## Official references

- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Apple Accessibility API](https://developer.apple.com/documentation/applicationservices/axuielement_h)
- [Apple Quartz Window Services](https://developer.apple.com/documentation/coregraphics/quartz-window-services)
- [Apple CGPreflightScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/cgpreflightscreencaptureaccess())
- [PyObjC Quartz notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html)

## License

MIT License. See [LICENSE](LICENSE).

## File editing and search (0.3.0)

Use `search_files` or `search_text` to narrow the scope, then inspect text with `read_text_file` and pass that exact string `version` as `edit_text_file.expected_version`. Editing requires one unique exact match; reread after a conflict. Changed files are backed up, and untouched bytes, BOM and UTF-16 byte order are retained. Searches have result, scan and time limits; inspect skipped/truncated fields. These three tools do not require command opt-in. See [parameters, encodings and boundaries](docs/file-tools.md).

On macOS, replacement refuses files with ACLs or extended attributes, including resource forks. Failure to inspect either metadata type also refuses the write. Ordinary file mode and executable bits are preserved.

### Local recovery and LaunchAgent

`Restore.command list` lists newest backups first. `restore <index>` restores content only, requests confirmation through stdin when the destination exists, and first backs up that live content. It preserves the current ordinary mode but does not restore historical ownership, ACLs or timestamps; unsafe metadata is refused. It obeys local pause and protected paths.

`LaunchDaemon.command` is a **user LaunchAgent** entry point despite its historical filename; never install it as a root daemon. Point a locally managed plist at the script and use `KeepAlive.SuccessfulExit=false`. Do not start it concurrently with `Start.command`; the existing-process check is not a startup mutex. Its atomic record names the intended Tunnel executable before exec handoff. Keychain/TCC interaction still requires physical-Mac validation.
