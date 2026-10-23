# 工具开发

工具是 Agent 可调用的能力单元。每个工具继承 `weci_sdk.Tool`。

## 基本结构

```python
from pydantic import BaseModel, Field
from weci_sdk import Tool, ToolResult


class SearchArgs(BaseModel):
    query: str = Field(..., description="搜索关键词。", max_length=500)
    limit: int = Field(default=5, description="返回结果数量。", ge=1, le=20)


class WebSearchTool(Tool):
    name = "web_search"
    description = "搜索互联网，返回相关结果。"
    args_model = SearchArgs

    async def execute(self, args: SearchArgs, context=None) -> ToolResult:
        # ... 实现搜索逻辑 ...
        return ToolResult.success(results)
```

## 参数 Schema

`args_model`（Pydantic BaseModel）是参数的**单一真源**：
- `get_schema()` 自动从 `args_model` 派生 OpenAI `parameters` JSON Schema
- 执行时自动用 `model_validate()` 校验参数

```python
class Args(BaseModel):
    name: str = Field(..., description="名字")       # 必填
    age: int = Field(default=18, ge=0)                # 可选，有默认值和约束
```

无参数工具：

```python
class NoArgsTool(Tool):
    name = "no_args"
    description = "不需要参数的工具"
    args_model = BaseModel    # 直接用 BaseModel 表示无参数

    async def execute(self, args, context=None):
        return ToolResult.success("done")
```

## 返回值

`execute` 可以返回：

| 类型 | 处理方式 |
| --- | --- |
| `str` | 成功，output 为该字符串 |
| `dict` | 成功，output 为 JSON 字符串 |
| `ToolResult` | 按其 `success` / `content` / `error` 处理 |
| `None` | 成功，output 为空字符串 |

推荐使用 `ToolResult`：

```python
return ToolResult.success(data, count=len(data))
return ToolResult.error("搜索服务不可用")
```

## 错误处理

工具执行中的异常会被 `run()` 捕获并转换为 `ToolExecutionResult(ok=False, error=...)`，**不会抛给 Agent**。

但建议显式返回 `ToolResult.error()`，这样错误信息更可控：

```python
try:
    result = await do_something()
    return ToolResult.success(result)
except Exception as exc:
    return ToolResult.error(f"操作失败：{exc}")
```

## 工具命名

- 全局唯一，建议 `plugin_name.tool_name` 或短名
- 小写字母、数字、下划线
- 名称冲突会导致注册失败

## 上下文（context）

`execute` 接收的 `context` 是 `ToolContext`，提供受控能力。需要对应权限才能调用：

```python
async def execute(self, args, context=None):
    if context is None:
        return ToolResult.error("no context")
    resp = await context.http.request("GET", "https://api.example.com")
    return ToolResult.success(resp["json"])
```

## 给模型的描述

`description` 决定模型何时调用工具。写清楚：
- 工具做什么
- 什么时候该用
- 什么时候不该用

```python
description = "搜索互联网获取实时信息。当用户询问最新事件、天气、新闻等需要实时数据的问题时使用。"
```
