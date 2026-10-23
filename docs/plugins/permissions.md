# 权限系统

插件在 `manifest.json` 的 `permissions` 字段声明所需能力。Core 在创建 `PluginContext` 时按声明授予，未声明的能力调用会抛 `PermissionDenied`。

## 权限列表

| 权限 | 能力 | context 方法 |
| --- | --- | --- |
| `network` | 发起 HTTP 请求 | `context.http.request()` / `context.http_request()` |
| `storage` | 读写插件私有 KV 存储 | `context.storage.get/set()` / `context.storage_get/set()` |
| `filesystem.read` | 读取文件、列目录 | `context.files.read/list_dir()` / `context.read_file/list_dir()` |
| `filesystem.write` | 写入文件 | `context.files.write()` / `context.write_file()` |
| `process` | 执行 shell 命令 | `context.run_command()` |
| `clipboard` | 读写系统剪贴板 | `context.clipboard_get/set()` |

## 声明权限

```json
{
  "permissions": ["network", "filesystem.read"]
}
```

## 权限拒绝

未声明权限时调用对应能力会抛 `PermissionDenied`（`PermissionError` 的子类）：

```python
# 插件未声明 network 权限
await context.http.request("GET", url)
# → PermissionDenied: 插件 'my-plugin' 未声明 'network' 权限，无法调用 context.http_request。
```

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

**但它不是 Python 沙箱**。恶意插件可以绕过 context 直接调用 Python 标准库（`subprocess`、`os` 等）。

> **Installing a plugin means trusting its code.**

不要安装来源不明的插件。真正的隔离（subprocess / WASM / container）属于未来工作。
