# macOS Local MCP

[简体中文](README.zh-CN.md) · [English](README.en.md)

**中文：** 让 ChatGPT 在 Chat 模式中也可以通过 MCP 读取本机文件、写入文件、查看屏幕并操作 macOS 桌面。当前版本为 0.3.0。

**English:** Give ChatGPT in Chat mode MCP-based access to local files, file writes, screen viewing, and macOS desktop control. Current version: 0.3.0.

**命令行 / Commands:** `command_start`、`command_poll`、`command_cancel` 支持本机命令、查询和取消，默认关闭，只能在本机授权。可选 Seatbelt `workspace-write` / `read-only` 配置，默认断网，可显式 `network=true`；标准设备和临时目录例外见完整说明。这不是完整权限隔离。

Opt-in commands support start, output/status polling and cancellation without terminal focus. Optional Seatbelt profiles reduce filesystem writes and deny network unless explicitly enabled with `network=true`; they are not a hard isolation boundary. See the full documentation for temporary-directory, device and SSH-agent exceptions.

**编辑与搜索 / Editing and search:** 保留 `edit_text_file(old_text, new_text, expected_version)` 的必需版本校验和字节保留语义；`search_files` / `search_text` 提供有界文件名和字面文本搜索。新增 `glob_files`、`file_hash`，窗口级截图和鼠标边界校验。

New tools: `glob_files` and `file_hash`, alongside the existing versioned editing and bounded literal-search interfaces. `desktop_screenshot(target_window=true)` captures a verified locked window; pointer input is bounded to the target or its verified modal dialog. Setup verifies the Tunnel download SHA256. Local restore backs up the live file before overwriting it.

- 中文完整说明：[README.zh-CN.md](README.zh-CN.md)
- Full English documentation: [README.en.md](README.en.md)
- 安全说明 / Security: [SECURITY.zh-CN.md](SECURITY.zh-CN.md) · [SECURITY.en.md](SECURITY.en.md)
- 验证记录 / Validation: [VALIDATION.zh-CN.md](VALIDATION.zh-CN.md) · [VALIDATION.en.md](VALIDATION.en.md)
- License: MIT

## 权限与风险 / Permissions and risk

**中文：** 这是高权限本机工具，不是操作系统沙箱。文件工具拥有当前 macOS 用户本来具有的文件权限；授予 **Accessibility** 后宿主进程可以控制普通应用，授予 **Screen Recording** 后可以读取屏幕和其他应用窗口内容。macOS 的 TCC 权限通常授予实际运行 MCP 的 Terminal、iTerm 或其他宿主，因此授权范围可能大于单个 Python 脚本。目标锁、暂停、备份和审计只能降低误操作风险。处理密码管理器、支付、敏感账号、医疗/财务信息或重要生产数据时应暂停服务。

**English:** This is a high-privilege local tool, not an operating-system sandbox. File tools operate with the current macOS user's file permissions. Granting **Accessibility** lets the host process control ordinary applications, while **Screen Recording** lets it observe screen and window contents. macOS TCC permissions are generally granted to the actual Terminal/iTerm/host process running MCP, so the permission scope can be broader than one Python script. Target locking, pause controls, backups, and audit logs reduce mistakes but do not create a low-privilege boundary. Pause the service around password managers, payments, sensitive accounts, health/financial information, or important production data.

See [SECURITY.zh-CN.md](SECURITY.zh-CN.md) / [SECURITY.en.md](SECURITY.en.md) for details.

## File editing and search / 文件编辑与搜索

0.3.0 新增 `edit_text_file`、`search_files` 和 `search_text`，支持带版本检查的精确局部编辑、递归文件名搜索和有界文本搜索。直接使用文件接口，不要求开启命令执行。用法、编码和边界见[文件工具说明](docs/file-tools.md)。

Version 0.3.0 adds exact partial edits with read-associated versions, recursive filename search, and bounded literal text search. Command opt-in is not required. See [file tools, encodings and limits](docs/file-tools.md).
