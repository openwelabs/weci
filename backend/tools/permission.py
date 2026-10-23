"""工具权限检查。

Agent 调用工具前必须经过 Permission Check。默认策略：
  - calculator / current_time 默认允许；
  - web_search 在没有真实 Provider 时由工具自身返回“未配置”，
    但权限层默认也放行（因为工具不会真正发起搜索）。
未来可替换为按用户/会话/工具粒度的策略。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolPermission(ABC):
    @abstractmethod
    async def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """返回 True 表示允许执行；False 表示拒绝。"""
        raise NotImplementedError


class AllowListPermission(ToolPermission):
    """只允许在白名单中的工具执行。"""

    def __init__(self, allowed: set[str]) -> None:
        self._allowed = set(allowed)

    async def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return tool_name in self._allowed


class AlwaysAllowPermission(ToolPermission):
    """全部放行（仅用于受信任的本地环境或测试）。"""

    async def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return True


# 便捷常量
always_allow = AlwaysAllowPermission()
