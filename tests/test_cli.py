"""CLI plugin 命令测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cli import build_parser


def _run(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# plugin list
# ---------------------------------------------------------------------------
def test_plugin_list_runs():
    # 不应抛异常，退出码 0
    rc = _run(["plugin", "list"])
    assert rc == 0


def test_plugin_list_json():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(["plugin", "list", "--json"])
    assert rc == 0
    out = buf.getvalue()
    data = json.loads(out)
    assert isinstance(data, list)
    # 至少有内置插件
    ids = [p["id"] for p in data]
    assert "calculator" in ids


# ---------------------------------------------------------------------------
# plugin info
# ---------------------------------------------------------------------------
def test_plugin_info_builtin():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(["plugin", "info", "calculator"])
    assert rc == 0
    out = buf.getvalue()
    assert "calculator" in out.lower() or "Calculator" in out


def test_plugin_info_nonexistent():
    rc = _run(["plugin", "info", "does-not-exist-xyz"])
    assert rc == 1


def test_plugin_info_json():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(["plugin", "info", "calculator", "--json"])
    assert rc == 0
    data = json.loads(buf.getvalue())
    assert data["id"] == "calculator"
    assert "lifecycle_state" in data


# ---------------------------------------------------------------------------
# plugin validate
# ---------------------------------------------------------------------------
def test_plugin_validate_hello():
    import io
    import contextlib

    hello_path = str(Path(__file__).resolve().parent.parent / "examples" / "plugins" / "hello")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(["plugin", "validate", hello_path])
    assert rc == 0
    assert "校验通过" in buf.getvalue()


def test_plugin_validate_template():
    import io
    import contextlib

    tpl_path = str(Path(__file__).resolve().parent.parent / "examples" / "plugins" / "template")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _run(["plugin", "validate", tpl_path])
    assert rc == 0


def test_plugin_validate_missing_manifest(tmp_path: Path):
    rc = _run(["plugin", "validate", str(tmp_path)])
    assert rc == 1


def test_plugin_validate_invalid_manifest(tmp_path: Path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "manifest.json").write_text("{bad")
    rc = _run(["plugin", "validate", str(d)])
    assert rc == 1


# ---------------------------------------------------------------------------
# plugin enable / disable (对内置 calculator 操作)
# ---------------------------------------------------------------------------
def test_plugin_disable_then_enable():
    # disable
    rc = _run(["plugin", "disable", "calculator"])
    assert rc == 0
    # enable back
    rc = _run(["plugin", "enable", "calculator"])
    assert rc == 0


def test_plugin_enable_nonexistent():
    rc = _run(["plugin", "enable", "no-such-plugin-xyz"])
    assert rc == 1
