"""Workspace 文件安全边界。

所有 Workspace 文件操作（读 / 写 / 删）都必须先经过本模块的
``WorkspaceGuard``，再做权限判定。它解决三件事：

  1. **路径逃逸**：``../``、绝对路径、符号链接都不能把访问带出 Root。
  2. **规范化后复查**：先 ``resolve()`` 得到真实路径，再判断是否仍在 Root 内，
     避免 ``a/../../b`` 这类规范化前看起来合法的路径绕过检查。
  3. **权限语义区分**：read / write / delete 使用不同权限，删除永远比写入更危险。

Workspace Tool 无法通过构造路径绕过这里的限制，因为 Core 的
``PluginContextImpl`` 在真正读写文件前会调用本模块。
"""

from __future__ import annotations

from pathlib import Path

from backend.config import PROJECT_DIR, settings


class WorkspaceSecurityError(PermissionError):
    """Workspace 访问越界或路径非法。"""

    def __init__(self, message: str, *, permission: str = "") -> None:
        self.permission = permission
        super().__init__(message)


class WorkspaceGuard:
    """把文件路径约束在 Workspace Root 之内。"""

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
        """把请求路径解析为 Root 内的真实路径。

        Args:
            path: 用户 / 工具提供的路径（相对或绝对）。
            permission: 用于错误信息标注的权限名。
            must_exist: 为 True 时要求目标已存在（读 / 删）；写允许不存在。
        """
        raw = str(path if path is not None else "").strip()
        if not raw:
            raise WorkspaceSecurityError("路径不能为空。", permission=permission)
        if "\x00" in raw:
            raise WorkspaceSecurityError("路径包含非法字符。", permission=permission)

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self._root / candidate

        # resolve() 会展开符号链接并消除 ..，解析后再判断是否仍在 Root 内
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceSecurityError(
                f"无法解析路径：{exc}", permission=permission
            ) from exc

        if not _is_within(resolved, self._root):
            raise WorkspaceSecurityError(
                "路径超出 Workspace 允许的范围，操作已被拒绝。",
                permission=permission,
            )

        if must_exist and not resolved.exists():
            raise WorkspaceSecurityError("文件不存在，操作已被拒绝。", permission=permission)
        return resolved

    def relative(self, path: str | Path, *, permission: str = "workspace:read") -> str:
        """返回相对 Root 的展示路径（不暴露服务器绝对路径）。"""
        resolved = self.resolve(path, permission=permission)
        try:
            return str(resolved.relative_to(self._root)) or "."
        except ValueError:  # pragma: no cover — resolve 已保证在 Root 内
            return resolved.name

    # ------------------------------------------------------------------
    # 便捷校验
    # ------------------------------------------------------------------
    def for_read(self, path: str | Path) -> Path:
        return self.resolve(path, permission="workspace:read", must_exist=True)

    def for_write(self, path: str | Path) -> Path:
        resolved = self.resolve(path, permission="workspace:write")
        if resolved == self._root:
            raise WorkspaceSecurityError(
                "不能把 Workspace 根目录当作文件写入。", permission="workspace:write"
            )
        if resolved.is_dir():
            raise WorkspaceSecurityError(
                "目标是一个目录，无法写入。", permission="workspace:write"
            )
        return resolved

    def for_delete(self, path: str | Path) -> Path:
        resolved = self.resolve(path, permission="workspace:delete", must_exist=True)
        if resolved == self._root:
            raise WorkspaceSecurityError(
                "不能删除 Workspace 根目录。", permission="workspace:delete"
            )
        return resolved

    def for_listing(self, path: str | Path) -> Path:
        resolved = self.resolve(path, permission="workspace:read")
        if not resolved.is_dir():
            raise WorkspaceSecurityError(
                "目标不是目录，无法列出内容。", permission="workspace:read"
            )
        return resolved

    # ------------------------------------------------------------------
    # 供 UI 展示
    # ------------------------------------------------------------------
    def describe(self) -> dict[str, str]:
        return {
            "root": str(self._root),
            "name": self._root.name,
        }


def _is_within(path: Path, root: Path) -> bool:
    """path 是否等于 root 或位于 root 之下（均已完成 resolve）。"""
    if path == root:
        return True
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["WorkspaceGuard", "WorkspaceSecurityError"]
