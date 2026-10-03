"""サンプルモデル shizuku の Look（looks/shizuku/look.json。チケット S-2）の確認。Maya 不要。

Look が無い環境（shizuku の取り込み前）ではスキップする。
"""

import re
from pathlib import Path

import pytest

from tdrive_toon import look

LOOK_PATH = Path(__file__).resolve().parents[1] / "looks" / "shizuku" / "look.json"

pytestmark = pytest.mark.skipif(not LOOK_PATH.exists(), reason="looks/shizuku/look.json なし（S-2 未実施）")

# 部位 → (ロール, マテリアル)。tools/setup_sample_shizuku_look.py の割り当てと同じ
EXPECTED_PARTS = {
    "skin": ("skin", ["mat_body01"]),
    "faceOption": ("mouth", ["mat_faceOption1"]),
    "hair": ("hair", ["mat_hair01"]),
    "wear01": ("cloth", ["mat_wear01"]),
    "wear02": ("cloth", ["mat_wear02"]),
    "wear03": ("cloth", ["mat_wear03"]),
    "watchLcd": ("accessory", ["lambert2"]),
}
# 非表示のブレンドシェイプのターゲットメッシュ（mdl_* 以外）に付くマテリアルは Look に入れない
FORBIDDEN_MATERIALS = {"lambert1", "standardSurface1", "openPBR_shader1", "particleCloud1"}


@pytest.fixture(scope="module")
def lk():
    return look.load(LOOK_PATH)


def test_validates(lk):
    assert look.validate(lk) == []
    assert lk["character"] == "shizuku"


def test_parts_and_roles(lk):
    assert set(lk["parts"]) == set(EXPECTED_PARTS)
    for part, (role, mats) in EXPECTED_PARTS.items():
        assert lk["parts"][part]["role"] == role, part
        assert sorted(lk["parts"][part]["materials"]) == mats, part


def test_no_hidden_target_materials(lk):
    names = set(lk["materials"])
    assert names == {m for _, mats in EXPECTED_PARTS.values() for m in mats}
    assert not names & FORBIDDEN_MATERIALS


def _texture_paths(lk) -> list[str]:
    paths = []
    for mat in lk["materials"].values():
        for key in ("albedo", "normal", "emission"):
            if mat["common"].get(key):
                paths.append(mat["common"][key])
        paths += [v for v in mat["specific"].values() if isinstance(v, str) and ("/" in v or "\\" in v)]
    return paths


def test_textures_are_project_relative(lk):
    paths = _texture_paths(lk)
    assert paths, "テクスチャが 1 枚も割り当たっていない"
    for p in paths:
        assert not re.match(r"^[A-Za-z]:", p) and not p.startswith(("/", "\\")), p
        assert "\\" not in p and ".." not in p.split("/"), p
        assert p.startswith("assets/shizuku/textures/"), p
        assert "yamag" not in p.lower() and "users" not in p.lower(), p
    # 絶対パス・ユーザー名はファイル全体にも入っていない（model など他の項目も）
    text = LOOK_PATH.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\/]", text) and "yamag" not in text.lower()


def test_albedo_and_normal(lk):
    for mat in ("mat_body01", "mat_faceOption1", "mat_hair01", "mat_wear01", "mat_wear02", "mat_wear03"):
        assert lk["materials"][mat]["common"]["albedo"].endswith("_d.png"), mat
    for mat in ("mat_body01", "mat_wear01", "mat_wear02", "mat_wear03"):
        assert lk["materials"][mat]["common"]["normal"].endswith("_n.png"), mat
    assert lk["features"]["normalMap"] is True
    # マスク `_m` は意味が不明なので割り当てない
    assert not any("_m." in p for p in _texture_paths(lk))
