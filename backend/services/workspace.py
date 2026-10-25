"""Workspace 路径解析。

WeCi 不再对文件路径做任何硬性限制：绝对路径、``..``、符号链接以及
Workspace 之外的路径都会直接交给操作系统处理。``WorkspaceGuard`` 现在只
负责把用户 / 工具提供的路径规范化（``expanduser`` + ``resolve``），不再
判断路径是否在 Root 之内，也不再在执行前拒绝操作。

真正的边界来自操作系统本身的文件权限与文件系统状态；WeCi 只会在操作
失败后把错误如实返回并记录日志。
"""

from __future__ import annotations

from pathlib import Path

from backend.config import PROJECT_DIR, settings


class WorkspaceSecurityError(PermissionError):
    """历史错误类型（保留以兼容既有导入，当前不再用于路径拦截）。"""

    def __init__(self, message: str, *, permission: str = "") -> None:
        self.permission = permission
        super().__init__(message)


class WorkspaceGuard:
    """把请求路径规范化为真实路径（不做 Root 范围限制）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root or settings.workspace_root or PROJECT_DIR).resolve()

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------
    def resolve(
        self, path: str | Path, *, permission: str = "workspace:read", must_exist: bool = False
    ) -> Path:
        """把请求路径解析为真实路径。

        相对路径相对 Workspace Root 解析；绝对路径、``..`` 与符号链接按
        操作系统语义直接展开。``must_exist`` / ``permission`` 仅为签名兼容，
        不再触发任何执行前拒绝。
        """
        raw = str(path if path is not None else "").strip()
        if not raw:
            raise ValueError("路径不能为空。")

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self._root / candidate
        return candidate.resolve(strict=False)

    def relative(self, path: str | Path, *, permission: str = "workspace:read") -> str:
        """返回相对 Root 的展示路径；在 Root 之外时返回绝对路径。"""
        resolved = self.resolve(path, permission=permission)
        try:
            return str(resolved.relative_to(self._root)) or "."
        except ValueError:
            return str(resolved)

    # ------------------------------------------------------------------
    # 便捷方法（保持既有调用方兼容，不再做路径限制）
    # ------------------------------------------------------------------
    def for_read(self, path: str | Path) -> Path:
        return self.resolve(path, permission="workspace:read")

    def for_write(self, path: str | Path) -> Path:
        return self.resolve(path, permission="workspace:write")

    def for_delete(self, path: str | Path) -> Path:
        return self.resolve(path, permission="workspace:delete")

    def for_listing(self, path: str | Path) -> Path:
        return self.resolve(path, permission="workspace:read")

    # ------------------------------------------------------------------
    # 供 UI 展示
    # ------------------------------------------------------------------
    def describe(self) -> dict[str, str]:
        return {
            "root": str(self._root),
            "name": self._root.name,
        }


__all__ = ["WorkspaceGuard", "WorkspaceSecurityError"]
