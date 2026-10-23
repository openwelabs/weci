# Hello Plugin（WeCi 示例插件）

一个最小可用的 WeCi 插件，演示 Plugin API 的基本用法。

## 功能

提供 `hello_world` 工具：

```
hello_world(name="World")  →  "Hello, World!"
```

## 安装

把本目录复制到用户插件目录：

```bash
mkdir -p ~/.weci/plugins
cp -r examples/plugins/hello ~/.weci/plugins/
```

或使用 WeCi CLI：

```bash
weci plugin install examples/plugins/hello
```

重启 WeCi（或在设置页的 Plugins 中启用）后，Agent 即可调用 `hello_world`。

## 结构

```
hello/
├── manifest.json   # 插件清单（id、版本、权限、入口）
├── plugin.py       # 插件实现：WeCiPlugin 子类 + Tool
└── README.md
```

## 核心代码

`plugin.py`：

```python
from pydantic import BaseModel, Field
from sdk import Tool, WeCiPlugin

class HelloArgs(BaseModel):
    name: str = Field(default="World", description="要问候的名字。")

class HelloWorldTool(Tool):
    name = "hello_world"
    description = "向指定名字打招呼。"
    args_model = HelloArgs

    async def execute(self, args, context=None):
        return f"Hello, {args.name or 'World'}!"

class HelloPlugin(WeCiPlugin):
    def get_tools(self):
        return [HelloWorldTool()]
```

## 说明

- 插件**只**依赖 `sdk` 包，不导入 `backend.*` 内部模块。
- 工具参数用 Pydantic 模型声明，WeCi 自动生成 Schema 并校验输入。
- `context` 提供 logger / config / http / storage / events 等受控能力，
  需在 manifest 的 `permissions` 中声明对应权限（本示例无需任何权限）。
- 安装第三方插件即意味着信任其代码（Python 插件可执行任意代码），
  请勿安装来源不明的插件。
