# Plugin SDK API

第三方插件**只应**从 `weci_sdk` 导入，不应导入 `backend.*` 内部模块。

```python
from weci_sdk import WeCiPlugin, Tool, ToolResult, ToolContext, PluginContext
from weci_sdk.permissions import KNOWN_PERMISSIONS, PermissionDenied
```

> 为兼容历史代码，也可从 `sdk` 导入（`sdk` 是 `weci_sdk` 的别名包）。新代码请使用 `weci_sdk`。

## 类总览

| 类 | 说明 |
| --- | --- |
| `WeCiPlugin` | 插件基类，实现 `on_load` / `on_unload` / `get_tools` |
| `Tool` | 工具基类，声明 `name` / `description` / `args_model` / `execute` |
| `ToolResult` | 工具执行结果（`success` / `content` / `error` / `metadata`） |
| `ToolExecutionResult` | Registry 层返回类型（向后兼容，含 `ok` / `output` / `duration_ms`） |
| `ToolContext` | 工具执行时的上下文接口 |
| `PluginContext` | 插件完整上下文（`ToolContext` 的超集） |
| `PermissionDenied` | 未授权能力调用时抛出 |

## WeCiPlugin

```python
class MyPlugin(WeCiPlugin):
    def on_load(self, context: PluginContext) -> None:
        """插件加载后调用。初始化资源、订阅事件。"""
        context.logger.info("loaded")

    def on_unload(self) -> None:
        """插件卸载前调用。释放资源。"""

    def get_tools(self) -> list[Tool]:
        """返回插件提供的工具列表。"""
        return [MyTool()]
```

元信息属性（来自 manifest，只读）：
- `self.metadata` — 完整 manifest dict
- `self.id` / `self.name` / `self.version` / `self.api_version` / `self.author`

## Tool

```python
class MyTool(Tool):
    name = "my_tool"                    # 全局唯一
    description = "工具描述"             # 给模型看
    args_model = MyArgs                 # Pydantic BaseModel

    async def execute(self, args: MyArgs, context: ToolContext | None = None) -> ToolResult:
        return ToolResult.success("结果")
```

方法：
- `get_schema()` → 返回 OpenAI parameters JSON Schema
- `to_openai_schema()` → 返回完整 function calling schema
- `execute(args, context)` → 工具逻辑，返回 `str` / `dict` / `ToolResult`
- `run(arguments, context)` → 内部调用，参数解析+校验+执行，返回 `ToolExecutionResult`（永不抛异常）

## ToolResult

```python
# 成功
ToolResult.success("Hello, World!")
ToolResult.success({"key": "value"}, source="api")

# 失败
ToolResult.error("something went wrong")
```

字段：
- `success: bool`
- `content: Any` — 成功时的内容
- `error: str | None` — 失败时的错误信息
- `metadata: dict` — 附加元数据

## PluginContext / ToolContext

插件通过 context 与 Core 交互，所有能力受权限约束。

```python
context.logger                  # 绑定插件 id 的 Logger
context.get_config(key, default) # 读取配置
context.plugin_id               # 插件 id
context.permissions             # 已授予的权限列表
```

子命名空间（推荐使用）：

```python
await context.http.request("GET", url)      # network
context.storage.get("key")                  # storage
context.storage.set("key", value)
context.files.read(path)                    # filesystem.read
context.files.write(path, content)          # filesystem.write
context.files.list_dir(path)
await context.run_command("ls")             # process
context.clipboard_get()                     # clipboard
context.clipboard_set("text")
```

扁平方法（向后兼容）：

```python
await context.http_request("GET", url)
context.storage_get("key")
context.storage_set("key", value)
context.read_file(path)
context.write_file(path, content)
context.list_dir(path)
await context.run_command("ls")
context.clipboard_get()
context.clipboard_set("text")
```

事件总线：

```python
context.emit("my_event", {"data": 1})
context.on("other_event", handler)
```
