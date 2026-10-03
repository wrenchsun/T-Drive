"""Unity パッケージ（unity/com.tdrive.facial）の配布形式の整合（D-Drive の更新ウィンドウ向け。docs/06）。"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "unity" / "com.tdrive.facial"
XYZ = re.compile(r"^\d+\.\d+\.\d+$")


def _pkg():
    return json.loads((PKG / "package.json").read_text(encoding="utf-8"))


def test_ddrive_update_declaration():
    decl = _pkg()["ddriveUpdate"]
    assert set(decl) <= {"requires", "compatibleWith"}
    for key, deps in decl.items():
        assert isinstance(deps, dict), key
        for name, ver in deps.items():
            assert isinstance(ver, str) and XYZ.match(ver), f"{key}.{name}={ver!r}"
    assert XYZ.match(decl["compatibleWith"]["com.ddrive.core"])


def test_package_version_equals_root_version():
    assert _pkg()["version"] == (REPO / "VERSION").read_text(encoding="utf-8").strip()


def test_package_changelog_has_unreleased_compat_first():
    text = (PKG / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert m, "## [Unreleased] が無い"
    assert re.match(r"\s*### 互換性\n", m[1]), "### 互換性 が最初の節でない"
