"""Manifest 校验与 API version 兼容性测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.plugins.manifest import (
    SUPPORTED_API_VERSIONS,
    Manifest,
    ManifestError,
    load_manifest,
)


# ---------------------------------------------------------------------------
# 1. 必填字段
# ---------------------------------------------------------------------------
def test_required_fields_all_present():
    m = Manifest.from_dict({
        "id": "hello", "name": "Hello", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    })
    assert m.id == "hello"
    assert m.api_version == "0.1"


@pytest.mark.parametrize("missing", ["id", "name", "version", "api_version", "entry"])
def test_missing_required_field_raises(missing: str):
    data = {
        "id": "hello", "name": "Hello", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    }
    del data[missing]
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict(data)
    assert missing in str(exc.value)


# ---------------------------------------------------------------------------
# 2. id 格式
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_id", ["", "A", "1abc", "a" * 65, "a b", "a.b"])
def test_invalid_id_raises(bad_id: str):
    with pytest.raises(ManifestError):
        Manifest.from_dict({
            "id": bad_id, "name": "x", "version": "0.1.0",
            "api_version": "0.1", "entry": "plugin.py",
        })


@pytest.mark.parametrize("good_id", ["ab", "hello", "my-plugin", "my_plugin", "a" * 64])
def test_valid_id(good_id: str):
    m = Manifest.from_dict({
        "id": good_id, "name": "x", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    })
    assert m.id == good_id


# ---------------------------------------------------------------------------
# 3. version 语义化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_ver", ["1", "1.2", "v1.2.3", "1.2.3.4", "abc"])
def test_invalid_version_raises(bad_ver: str):
    with pytest.raises(ManifestError):
        Manifest.from_dict({
            "id": "pp", "name": "p", "version": bad_ver,
            "api_version": "0.1", "entry": "plugin.py",
        })


@pytest.mark.parametrize("good_ver", ["0.1.0", "1.2.3", "0.1.0-beta.1", "1.0.0+build.1"])
def test_valid_version(good_ver: str):
    m = Manifest.from_dict({
        "id": "pp", "name": "p", "version": good_ver,
        "api_version": "0.1", "entry": "plugin.py",
    })
    assert m.version == good_ver


# ---------------------------------------------------------------------------
# 4. api_version 兼容性
# ---------------------------------------------------------------------------
def test_unsupported_api_version_raises():
    with pytest.raises(ManifestError) as exc:
        Manifest.from_dict({
            "id": "pp", "name": "p", "version": "0.1.0",
            "api_version": "9.9", "entry": "plugin.py",
        })
    assert "api_version" in str(exc.value)


def test_supported_api_version_is_0_1():
    assert "0.1" in SUPPORTED_API_VERSIONS


# ---------------------------------------------------------------------------
# 5. entry 路径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_entry", ["/etc/passwd", "../evil.py", "a/../../b.py", ""])
def test_invalid_entry_raises(bad_entry: str):
    with pytest.raises(ManifestError):
        Manifest.from_dict({
            "id": "pp", "name": "p", "version": "0.1.0",
            "api_version": "0.1", "entry": bad_entry,
        })


def test_valid_entry():
    m = Manifest.from_dict({
        "id": "pp", "name": "p", "version": "0.1.0",
        "api_version": "0.1", "entry": "src/plugin.py",
    })
    assert m.entry == "src/plugin.py"


# ---------------------------------------------------------------------------
# 6. permissions
# ---------------------------------------------------------------------------
def test_unknown_permission_raises():
    with pytest.raises(ManifestError):
        Manifest.from_dict({
            "id": "pp", "name": "p", "version": "0.1.0",
            "api_version": "0.1", "entry": "plugin.py",
            "permissions": ["network", "evil"],
        })


def test_valid_permissions():
    m = Manifest.from_dict({
        "id": "pp", "name": "p", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
        "permissions": ["network", "filesystem.read"],
    })
    assert m.permissions == ["network", "filesystem.read"]


# ---------------------------------------------------------------------------
# 7. 未知字段保留（向前兼容）
# ---------------------------------------------------------------------------
def test_unknown_fields_kept_in_raw():
    m = Manifest.from_dict({
        "id": "pp", "name": "p", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
        "custom_field": "hello",
        "nested": {"a": 1},
    })
    assert m.unknown_fields.get("custom_field") == "hello"
    assert m.raw["custom_field"] == "hello"


# ---------------------------------------------------------------------------
# 8. load_manifest 从目录读取
# ---------------------------------------------------------------------------
def test_load_manifest_from_dir(tmp_path: Path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "id": "pp", "name": "pp", "version": "0.1.0",
        "api_version": "0.1", "entry": "plugin.py",
    }))
    m = load_manifest(d)
    assert m.id == "pp"


def test_load_manifest_missing_file_raises(tmp_path: Path):
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nonexistent")


def test_load_manifest_invalid_json_raises(tmp_path: Path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "manifest.json").write_text("{not json")
    with pytest.raises(ManifestError):
        load_manifest(d)
