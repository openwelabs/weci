# Manifest 规范

每个插件目录必须包含 `manifest.json`，声明插件元信息。

## 完整示例

```json
{
  "id": "hello",
  "name": "Hello Plugin",
  "version": "0.1.0",
  "api_version": "0.1",
  "description": "示例插件",
  "author": "WeCi Examples",
  "entry": "plugin.py",
  "permissions": ["network"],
  "config": {
    "timeout": 30
  },
  "default_enabled": true
}
```

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | ✅ | 全局唯一标识。小写字母开头，2-64 字符，仅含 `a-z0-9_-` |
| `name` | string | ✅ | 显示名称 |
| `version` | string | ✅ | 语义化版本（如 `0.1.0`、`1.2.3`、`0.1.0-beta.1`） |
| `api_version` | string | ✅ | Plugin API 版本，当前支持 `0.1` |
| `entry` | string | ✅ | 入口模块相对路径（如 `plugin.py`、`src/main.py`），不允许绝对路径或 `..` |
| `description` | string | ❌ | 插件描述 |
| `author` | string | ❌ | 作者 |
| `permissions` | string[] | ❌ | 声明所需权限，见 [permissions.md](permissions.md) |
| `config` | object | ❌ | 插件默认配置，可通过 `context.get_config(key)` 读取 |
| `default_enabled` | bool | ❌ | 首次安装时是否默认启用，默认 `true` |

## 校验规则

### id 格式

- 必须以小写字母 `a-z` 开头
- 后续字符可为 `a-z`、`0-9`、`-`、`_`
- 长度 2-64

合法：`hello`、`my-plugin`、`github_v2`
非法：`Hello`、`1abc`、`a.b`、`a`（太短）

### version 语义化

遵循 [SemVer](https://semver.org/)：`主.次.修订[-预发布][+构建]`

合法：`0.1.0`、`1.2.3`、`0.1.0-beta.1`、`1.0.0+build.1`
非法：`1`、`1.2`、`v1.2.3`、`abc`

### api_version 兼容性

必须在 Core 支持的版本集合内。当前支持 `0.1`。

### entry 路径

- 必须是插件目录内的相对路径
- 不允许绝对路径（`/` 开头）
- 不允许 `..` 上级引用

合法：`plugin.py`、`src/main.py`
非法：`/etc/passwd`、`../evil.py`

### permissions

必须全部为已知权限。未知权限会导致校验失败。

## 未知字段

manifest 中的未知字段会被保留在 `raw` 和 `unknown_fields` 中，不阻断加载（向前兼容）。插件可通过 `self.metadata` 读取。

## 错误处理

校验失败时抛出 `ManifestError`，错误信息对人类可读，例如：

- `manifest 缺少必填字段：api_version`
- `插件 id 'a' 不合法：须以小写字母开头...`
- `版本号 '1.2' 不合法：须为语义化版本`
- `不支持的 api_version '9.9'，当前支持：0.1`
- `未知权限：evil。支持：clipboard, filesystem.read, ...`
