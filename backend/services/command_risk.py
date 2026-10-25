"""命令风险分析器。

Agent 执行 Shell 命令前，先用本模块判断命令的**实际语义**，再由
``SecurityPolicyManager`` 决定是否需要用户确认。

设计要点：

1. **结构化分析，而不是字符串黑名单**。
   ``echo "rm -rf"`` 只是把一个字符串打印出来，命令名是 ``echo``，因此是安全命令；
   ``rm -rf ./dist`` 的命令名是 ``rm`` 且带递归参数，因此是危险命令。

2. **按 Shell 语法切分**。用 ``shlex`` 做 POSIX 分词，再按 ``;`` / ``&&`` /
   ``||`` / ``|`` / 换行切分命令段，识别管道、重定向、包装命令
   （``sudo`` / ``env`` / ``xargs`` / ``timeout``）与 ``sh -c`` 内层命令。

3. **可替换**。本模块只暴露 ``CommandRiskAnalyzer.analyze()``；未来若引入完整
   Shell AST parser，只需要替换这里的实现，Agent / Policy / UI 都不用改。

返回结构::

    CommandRisk(risk="dangerous", reason="递归删除文件", matched=["rm -rf"])

分析失败（例如引号不闭合）时按高风险处理，遵循 Default Deny 原则。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

RISK_SAFE = "safe"
RISK_DANGEROUS = "dangerous"

# 命令段分隔符（同时也是管道连接符）；括号用于子 Shell
_SEPARATORS = {"|", "||", "&&", ";", "&", "\n", "(", ")"}
# 重定向符（写）
_WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
_READ_REDIRECTS = {"<", "<<", "<<<"}

# 变量赋值前缀，例如 FOO=bar cmd
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash"}
_INLINE_CODE = {
    ("python", "-c"),
    ("python3", "-c"),
    ("node", "-e"),
    ("node", "--eval"),
    ("perl", "-e"),
    ("ruby", "-e"),
    ("php", "-r"),
    ("lua", "-e"),
}
# 只是改变执行方式的包装命令，真正需要分析的是后面的命令
_WRAPPERS = {"env", "command", "builtin", "nohup", "time", "exec", "nice", "ionice", "stdbuf", "xargs"}
# 直接提权的命令：本身即为高危
_PRIVILEGE = {"sudo", "su", "doas", "pkexec"}

# 系统目录：这些位置上的写入 / 删除 / 递归操作视为高风险
_SYSTEM_DIRS: tuple[str, ...] = (
    "/",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/var",
    "/opt",
    "/root",
    "/home",
    "/srv",
    "/run",
)

# 危险命令 → 中文说明
_DANGEROUS_COMMANDS: dict[str, str] = {
    "rm": "删除文件",
    "unlink": "删除文件",
    "rmdir": "删除目录",
    "shred": "安全擦除文件",
    "mv": "移动 / 覆盖文件",
    "chmod": "修改文件权限",
    "chown": "修改文件所有者",
    "chgrp": "修改文件所属组",
    "kill": "终止进程",
    "pkill": "终止进程",
    "killall": "终止进程",
    "reboot": "重启系统",
    "shutdown": "关闭系统",
    "poweroff": "关闭电源",
    "halt": "停机",
    "systemctl": "操作系统服务",
    "service": "操作系统服务",
    "dd": "直接磁盘写入",
    "fdisk": "磁盘分区操作",
    "parted": "磁盘分区操作",
    "mount": "挂载文件系统",
    "umount": "卸载文件系统",
    "truncate": "截断文件",
    "passwd": "修改账户凭据",
    "useradd": "修改系统账户",
    "userdel": "修改系统账户",
    "usermod": "修改系统账户",
    "groupadd": "修改系统账户",
    "crontab": "修改计划任务",
    "eval": "动态执行 Shell 代码",
}

# mkfs 有大量后缀（mkfs.ext4 / mkfs.xfs），单独按前缀识别
_MKFS_PREFIX = "mkfs."


@dataclass
class Finding:
    reason: str
    matched: str = ""


@dataclass
class CommandRisk:
    """一次命令风险评估的结果。"""

    risk: str = RISK_SAFE
    reason: str = ""
    matched: list[str] = field(default_factory=list)

    @property
    def dangerous(self) -> bool:
        return self.risk == RISK_DANGEROUS

    def to_dict(self) -> dict[str, object]:
        return {
            "risk": self.risk,
            "reason": self.reason,
            "matched": list(self.matched),
        }


class CommandRiskAnalyzer:
    """把 Shell 命令解析为结构化操作，并给出风险等级。"""

    def analyze(self, command: str | Iterable[str] | None) -> CommandRisk:
        if command is None:
            return CommandRisk()
        if isinstance(command, (list, tuple)):
            text = " ".join(str(item) for item in command)
        else:
            text = str(command)
        text = text.strip()
        if not text:
            return CommandRisk()

        try:
            tokens = _tokenize(text)
        except ValueError:
            # 引号不闭合 / 无法解析：保守处理为高风险
            return CommandRisk(
                risk=RISK_DANGEROUS,
                reason="无法解析命令结构，已按高风险处理。",
                matched=[text[:120]],
            )

        findings = self._analyze_tokens(tokens, text)
        if not findings:
            return CommandRisk()
        reasons: list[str] = []
        matched: list[str] = []
        for finding in findings:
            if finding.reason not in reasons:
                reasons.append(finding.reason)
            if finding.matched and finding.matched not in matched:
                matched.append(finding.matched)
        return CommandRisk(
            risk=RISK_DANGEROUS,
            reason="；".join(reasons),
            matched=matched,
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _analyze_tokens(self, tokens: list[str], raw: str) -> list[Finding]:
        findings: list[Finding] = []
        segments, operators = _split_segments(tokens)

        # 管道执行：curl / wget ... | sh
        for index, operator in enumerate(operators):
            if operator != "|":
                continue
            left = _command_name(segments[index])
            right = _command_name(segments[index + 1])
            if left in {"curl", "wget"} and right in _SHELLS:
                findings.append(Finding("网络下载后直接执行", f"{left} | {right}"))

        for segment in segments:
            if not segment:
                continue
            findings.extend(self._analyze_segment(segment))

        # sh -c "$(curl ...)" 这类命令替换：外层是 Shell 且内层是下载命令
        if re.search(r"(?i)\b(?:curl|wget)\b", raw) and re.search(
            r"(\$\(|`)", raw
        ):
            if any(f.reason == "执行内联代码" for f in findings) or any(
                _command_name(segment) in _SHELLS for segment in segments
            ):
                findings.append(Finding("网络下载后直接执行", "command substitution"))
        return findings

    def _analyze_segment(self, segment: list[str]) -> list[Finding]:
        findings: list[Finding] = []

        # 写重定向到系统目录
        for index, token in enumerate(segment):
            if token in _WRITE_REDIRECTS and index + 1 < len(segment):
                target = segment[index + 1]
                if _is_system_path(target):
                    findings.append(Finding("写入系统目录", f"> {target}"))

        command, args = _strip_wrappers(segment, findings)
        if not command:
            return findings
        name = _basename(command)

        # 提权命令在 _strip_wrappers 中已记录，继续分析其后的真实命令
        if name in _SHELLS:
            findings.extend(self._analyze_shell(args, segment))
            return findings

        if args and (name, args[0]) in _INLINE_CODE:
            findings.append(Finding("执行内联代码", f"{name} {args[0]}"))
            return findings

        if name.startswith(_MKFS_PREFIX) or name == "mkfs":
            findings.append(Finding("格式化文件系统", name))
            return findings

        if name == "find":
            findings.extend(self._analyze_find(args))
            return findings

        if name == "git":
            findings.extend(self._analyze_git(args))
            return findings

        reason = _DANGEROUS_COMMANDS.get(name)
        if reason is None:
            return findings

        if name in {"rm", "unlink", "rmdir", "shred"}:
            recursive = any(
                (arg == "--recursive" or (arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg)))
                for arg in args
            )
            targets = [arg for arg in args if not arg.startswith("-")]
            system_target = next((arg for arg in targets if _is_system_path(arg)), "")
            if system_target:
                findings.append(
                    Finding("对系统目录执行删除", f"{name} {system_target}")
                )
            elif recursive:
                flag = next(
                    (arg for arg in args if arg.startswith("-") and "r" in arg.lower()),
                    "-r",
                )
                findings.append(Finding("递归删除文件", f"{name} {flag}"))
            else:
                findings.append(Finding(reason, name))
            return findings

        findings.append(Finding(reason, name))
        return findings

    def _analyze_shell(self, args: list[str], segment: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        command = _basename(segment[0]) if segment else "sh"
        if "-c" in args:
            index = args.index("-c")
            if index + 1 < len(args):
                inner = args[index + 1]
                inner_risk = self.analyze(inner)
                if inner_risk.dangerous:
                    findings.append(
                        Finding(inner_risk.reason, f"{command} -c {inner[:80]}")
                    )
                return findings
            findings.append(Finding("执行 Shell 代码", f"{command} -c"))
            return findings
        if any(not arg.startswith("-") for arg in args):
            findings.append(Finding("执行 Shell 脚本", command))
        else:
            findings.append(Finding("启动交互式 Shell", command))
        return findings

    @staticmethod
    def _analyze_find(args: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        if "-delete" in args:
            findings.append(Finding("递归删除文件", "find -delete"))
        if "-exec" in args:
            index = args.index("-exec")
            if index + 1 < len(args):
                target = _basename(args[index + 1])
                if target in {"rm", "unlink", "shred", "rmdir"}:
                    findings.append(Finding("递归删除文件", f"find -exec {target}"))
        return findings

    @staticmethod
    def _analyze_git(args: list[str]) -> list[Finding]:
        if not args:
            return []
        sub = args[0]
        rest = args[1:]
        if sub == "reset" and "--hard" in rest:
            return [Finding("破坏性 git 操作", "git reset --hard")]
        if sub == "clean" and any(
            arg.startswith("-") and not arg.startswith("--") and "f" in arg for arg in rest
        ):
            return [Finding("删除未跟踪文件", "git clean -f")]
        if sub == "push" and ("--force" in rest or "-f" in rest):
            return [Finding("强制推送远程分支", "git push --force")]
        return []


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _tokenize(command: str) -> list[str]:
    """POSIX 分词，保留 ``|`` ``&&`` ``;`` ``>`` 等控制符为独立 token。"""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _split_segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """按控制符切分命令段，同时返回段之间的连接符。"""
    segments: list[list[str]] = []
    operators: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            segments.append(current)
            current = []
            operators.append(token)
        else:
            current.append(token)
    segments.append(current)
    return segments, operators


def _strip_wrappers(
    segment: list[str], findings: list[Finding]
) -> tuple[str, list[str]]:
    """去掉变量赋值与包装命令，返回真实命令名与参数。"""
    index = 0
    length = len(segment)
    while index < length:
        token = segment[index]
        if _ASSIGNMENT.match(token):
            index += 1
            continue
        name = _basename(token)
        if name in _PRIVILEGE:
            findings.append(Finding("提权执行", name))
            index += 1
            while index < length and segment[index].startswith("-"):
                index += 1
            continue
        if name in _WRAPPERS:
            index += 1
            while index < length and segment[index].startswith("-"):
                index += 1
            # env VAR=value ... / timeout 30 ... 需要再跳过一层
            while index < length and (
                _ASSIGNMENT.match(segment[index])
                or re.fullmatch(r"\d+(\.\d+)?[smhd]?", segment[index] or "")
            ):
                index += 1
            continue
        break
    if index >= length:
        return "", []
    return segment[index], segment[index + 1 :]


def _command_name(segment: list[str]) -> str:
    if not segment:
        return ""
    scratch: list[Finding] = []
    command, _ = _strip_wrappers(segment, scratch)
    return _basename(command) if command else ""


def _basename(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    return Path(text).name.lower()


def _is_system_path(value: str) -> bool:
    """是否是根目录 / 系统目录（含其子路径）。"""
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw in {"*", "/*", "~", "$HOME", "${HOME}"}:
        return True
    try:
        path = Path(raw).expanduser()
    except (OSError, RuntimeError):
        return False
    if not path.is_absolute():
        return False
    text = str(path)
    for base in _SYSTEM_DIRS:
        if base == "/":
            if text == "/":
                return True
            continue
        if text == base or text.startswith(base + "/"):
            return True
    return False


__all__ = [
    "CommandRisk",
    "CommandRiskAnalyzer",
    "RISK_SAFE",
    "RISK_DANGEROUS",
]
