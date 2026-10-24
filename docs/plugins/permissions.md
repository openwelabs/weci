# 权限系统

插件在 `manifest.json` 的 `permissions` 字段声明所需能力。Core 在创建 `PluginContext` 时按声明授予，未声明的能力调用会抛 `PermissionDenied`。

同时，**每一次工具调用都会先经过统一的 Permission Manager**：

```
Request → Permission Manager → Tool Registry → Tool / Plugin / MCP
```

内置工具、插件工具与 MCP 工具没有例外，Agent 不持有任何硬编码的权限分支。

## 权限列表

推荐使用带 scope 的权限名：

| 权限 | 风险 | 能力 | context 方法 |
| --- | --- | --- | --- |
| `workspace:read` | low | 读取文件、列目录 | `context.files.read/list_dir()` / `context.read_file/list_dir()` |
| `workspace:write` | medium | 写入 / 追加文件 | `context.files.write()` / `context.write_file/append_file()` |
| `workspace:delete` | high | 删除文件 | `context.delete_file()` |
| `command:execute` | high | 执行 shell 命令 | `context.run_command()` |
| `network:access` | medium | 发起 HTTP 请求 | `context.http.request()` / `context.http_request()` |
| `memory:read` | low | 读取长期记忆 | `context.memory_list()` |
| `memory:write` | medium | 写入 / 删除长期记忆 | `context.memory_save()` / `memory_delete()` |
| `storage:read` | low | 读取插件私有 KV | `context.storage.get()` / `storage_get()` |
| `storage:write` | low | 写入插件私有 KV | `context.storage.set()` / `storage_set()` |
| `clipboard:access` | medium | 读写系统剪贴板 | `context.clipboard_get/set()` |
| `plugin:use` | low | 使用插件提供的工具 | Tool Registry 统一判定 |
| `mcp:use` | medium | 使用 MCP Server 的工具 | Tool Registry 统一判定 |

### 旧权限名兼容

以下名称会在加载时通过 `normalize_permissions()` 自动归一化，现有插件无需修改：

| 旧名称 | 归一化后 |
| --- | --- |
| `network` | `network:access` |
| `filesystem.read` | `workspace:read` |
| `filesystem.write` | `workspace:write` |
| `filesystem.delete` | `workspace:delete` |
| `process` | `command:execute` |
| `clipboard` | `clipboard:access` |
| `memory` | `memory:read` + `memory:write` |
| `storage` | `storage:read` + `storage:write` |

## 声明权限

```json
{
  "permissions": ["network:access", "workspace:read"]
}
```

## 运行时判定

1. **Tool Metadata**：每个 Tool 通过类属性 `permissions` 声明所需权限，Tool Registry 统一读取。
2. **Default Deny**：没有声明的权限默认禁止。插件 manifest 中声明后，加载时按风险等级自动授予：
   - 低风险权限自动允许；
   - 中 / 高风险权限在 Agent 首次使用时请求用户确认。
3. **用户确认**：高风险操作会通过 SSE 下发 `permission_request`，前端展示
   「Agent 请求执行 …」，用户可以：
   - 仅本次允许（once）
   - 本次 Run 允许（run，Run 结束后失效）
   - 拒绝（deny）
4. **审计**：判定结果写入 Agent Run 的 ToolCallRecord（`permissions` /
   `permission_result` / `user_confirmation`）和安全日志。

## 权限拒绝

未声明权限时调用对应能力会抛 `PermissionDenied`（`PermissionError` 的子类）：

```python
# 插件未声明 network 权限
await context.http.request("GET", url)
# → PermissionDenied: 插件 'my-plugin' 未声明 'network:access' 权限，无法调用 context.http_request。
```

## Workspace 沙箱

所有 `context` 文件操作都会先经过 `WorkspaceGuard`：

- 空路径 / 含 NUL 的路径直接拒绝；
- 相对路径统一拼接到 Workspace Root 后 `resolve`；
- 规范化后再次校验 `relative_to(root)`，禁止 `../` 逃逸与 Root 外访问；
- 读、写、删除分别使用 `workspace:read` / `workspace:write` / `workspace:delete`；
- 删除操作额外记录 `workspace.file_deleted` 安全日志。

## 命令执行安全

`context.run_command()`：

- `cwd` 必须位于 Workspace 内，否则抛 `PermissionError`；
- 环境变量经 `sanitize_env()` 过滤（API Key / Token / Password 等不会注入）；
- 支持 `timeout`，超时后终止子进程并返回 `timed_out=True`；
- 任务取消时会终止子进程；
- 返回 `returncode` / `exit_code` / `stdout` / `stderr` / `command` / `cwd` / `duration_ms`；
- 成功的命令记录 `command.executed` 安全日志（命令文本已脱敏）。

## 插件存储隔离

每个插件的 storage 按 `plugin_id` 隔离：

```
.weci/plugins/data/
├── hello/
│   └── store.json
└── github/
    └── store.json
```

Plugin A 无法直接读取 Plugin B 的存储——每个 context 只能访问自己 `plugin_id` 对应的存储文件。

## 安全边界

⚠️ **重要**：第三方 Python 插件本质上可以执行任意 Python 代码。

权限系统是**能力控制接口**，提供：
- 声明式权限（manifest 中声明）
- 运行时拦截（未授权调用抛 `PermissionDenied`）
- 统一判定（所有工具经 Permission Manager）
- 有审计（Run 记录 + 脱敏安全日志）

**但它不是 Python 沙箱**。恶意插件可以绕过 context 直接调用 Python 标准库（`subprocess`、`os` 等）。

> **Installing a plugin means trusting its code.**

不要安装来源不明的插件。真正的隔离（subprocess / WASM / container）属于未来工作。
