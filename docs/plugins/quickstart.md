# 快速上手

5 分钟开发并运行你的第一个 WeCi 插件。推荐使用官方开发工具
[**weci-maker**](https://github.com/openwelabs/weci-maker)（定位类似 Cargo），
它负责创建项目、检查、打包和安装。

## 0. 安装 weci-maker

```bash
# weci-maker 复用 WeCi 的 Plugin SDK 与加载器，需先安装 WeCi
pip install -e ~/repos/weci
pip install -e ~/repos/weci-maker
```

## 1. 创建插件项目

```bash
weci-maker new my-plugin
cd my-plugin
```

`weci-maker new` 会把目录名规范化为合法的插件 id（小写字母开头，仅含
`a-z0-9_-`），并生成完整项目骨架：

```
my-plugin/
├── manifest.json      # 插件清单
├── plugin.py          # 入口模块：WeCiPlugin 子类 + Tool
├── README.md
└── tests/
    └── test_plugin.py # 插件自测试（不依赖 WeCi Core）
```

也可用 `weci-maker init` 在一个已有目录中初始化（不会覆盖已有文件）。

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

编辑 `plugin.py`（模板已生成一个回显工具，可直接改写）：

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

## 4. 检查

```bash
weci-maker check
```

检查步骤与 WeCi Loader 的实际加载流程一一对应（manifest → 入口文件 →
模块加载 → `WeCiPlugin` 子类 → 工具 schema），通过后会显示：

```
[OK] manifest 合法：id=my-plugin, version=0.1.0, api_version=0.1
[OK] 入口文件存在：plugin.py
[OK] 入口模块可加载
[OK] 找到插件类：MyPlugin
[OK] 工具: echo  参数: ['text']

检查通过：.../my-plugin  提供 1 个工具。
```

## 5. 运行插件自测试

生成的项目自带 pytest 测试，不依赖 WeCi Core：

```bash
python -m pytest tests/
```

## 6. 构建插件包

```bash
weci-maker build
```

`build` 会先运行完整 `check`（检查不过不打包），然后输出
`dist/my-plugin-0.1.0.wcp` 并打印 sha256。

`.wcp`（WeCi Package）是标准 ZIP 包，`manifest.json` 位于包根，
打包时自动排除 `__pycache__`、`dist`、`.git` 等。

## 7. 安装

```bash
weci-maker install dist/my-plugin-0.1.0.wcp
```

安装目标为用户插件目录 `~/.weci/plugins/<id>/`（可用环境变量
`WECI_PLUGINS_DIR` 覆盖），与 `weci plugin install <目录>` 效果一致。
同名插件已存在时用 `--force` 覆盖。

也可以直接安装插件目录（跳过打包）：

```bash
weci-maker install ./my-plugin
```

## 8. 验证

```bash
weci plugin list                 # 应能看到 my-plugin（loaded）
weci plugin info my-plugin       # 查看详情
```

重启 WeCi（`weci serve`），打开前端切到 **Agent 模式**，对 AI 说
"用 echo 工具回显 hello"，即可看到工具被调用。

## 不用 weci-maker 的手动流程

如果不想安装 weci-maker，也可以完全手动：

```bash
cp -r examples/plugins/template my-plugin   # 复制模板
# ... 编辑 manifest.json 和 plugin.py ...
weci plugin validate ./my-plugin            # 校验
weci plugin install ./my-plugin             # 安装到 ~/.weci/plugins/
weci plugin enable my-plugin                # 如默认未启用
```

两种方式等价：`weci-maker check` 与 `weci plugin validate` 使用同一份
manifest 校验规则，`weci-maker install` 与 `weci plugin install`
走同一个 `PluginLoader`。

## weci-maker 命令一览

| 命令 | 说明 |
| --- | --- |
| `weci-maker new <name>` | 创建插件项目（目录名自动规范化为插件 id） |
| `weci-maker init` | 在当前目录初始化插件项目 |
| `weci-maker check [path]` | 检查插件（manifest、入口模块、`WeCiPlugin` 子类、工具 schema） |
| `weci-maker build [path]` | 构建 `.wcp` 插件包（输出 `dist/<id>-<version>.wcp`） |
| `weci-maker install <pkg>` | 安装 `.wcp` 包或插件目录到用户插件目录 |

通用选项：`new`/`init` 支持 `--id`、`--name`、`--description`、`--author`；
`build` 支持 `-o <dir>`；`install` 支持 `--force`（覆盖同名插件）。
