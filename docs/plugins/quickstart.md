# 快速上手

5 分钟开发并运行你的第一个 WeCi 插件。

## 1. 复制模板

```bash
cp -r examples/plugins/template my-plugin
cd my-plugin
```

## 2. 修改 manifest.json

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "0.1.0",
  "description": "我的第一个 WeCi 插件",
  "author": "Your Name",
  "api_version": "0.1",
  "entry": "plugin.py",
  "permissions": []
}
```

> `id` 必须全局唯一，小写字母开头，2-64 字符，仅含 `a-z0-9_-`。

## 3. 实现工具

编辑 `plugin.py`：

```python
from pydantic import BaseModel, Field
from weci_sdk import Tool, ToolResult, WeCiPlugin


class EchoArgs(BaseModel):
    text: str = Field(..., description="要回显的文本。")


class EchoTool(Tool):
    name = "echo"
    description = "回显输入的文本。"
    args_model = EchoArgs

    async def execute(self, args: EchoArgs, context=None) -> ToolResult:
        return ToolResult.success(f"你说：{args.text}")


class MyPlugin(WeCiPlugin):
    def get_tools(self) -> list[Tool]:
        return [EchoTool()]
```

## 4. 安装前校验

```bash
weci plugin validate ./my-plugin
```

通过后会显示：
```
[OK] manifest 合法
[OK] 入口文件存在
[OK] 入口模块可加载
[OK] 找到插件类
[OK] 工具: echo 参数: ['text']
校验通过
```

## 5. 安装并启用

```bash
weci plugin install ./my-plugin
weci plugin enable my-plugin    # 如果默认未启用
```

## 6. 验证

```bash
weci plugin info my-plugin
weci plugin list
```

重启 WeCi（`weci serve`），Agent 即可看到 `echo` 工具。

## 7. 在 Agent 中测试

打开 WeCi 前端，切到 Agent 模式，对 AI 说"用 echo 工具回显 hello"。
